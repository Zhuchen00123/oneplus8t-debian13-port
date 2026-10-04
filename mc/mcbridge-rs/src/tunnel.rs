//! 隧道协议 + 服务端（CNB 侧）实现。
//!
//! 帧格式：`[type:1][id:4 BE][payload...]`
//!   1 OPEN   开一条新的 TCP 流（id 由发起方分配，全局唯一）
//!   2 DATA   id 流上的数据
//!   3 CLOSE  id 流结束
//!   4 PING   保活
//!   5 PONG   保活应答
//!
//! 关键设计：一条逻辑链路拆成两条**单向** WebSocket（`/up` 与 `/down`）。
//! 拨号方（DSW / 好友客户端）只写 up、只读 down；CNB 侧只读 up、只写 down。
//! 单向流可以由单个线程独占，不必在阻塞读的同一条流上加锁 —— 这是整个实现
//! 能这么短的原因（TLS 流无法 clone，双向单流就必须引入 poll 循环）。
//!
//! 路径约定：
//!   /t/<secret>/<session>/up | down    服务端隧道（DSW）
//!   /f/<token>/<session>/up  | down    好友转发器
//!   其它任意路径                        兼容模式，一条 WS == 一个玩家连接

use std::collections::HashMap;
use std::io::Write;
use std::net::{Shutdown, TcpListener, TcpStream};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crate::ws;
use crate::ws::WsStream;

pub const T_OPEN: u8 = 1;
pub const T_DATA: u8 = 2;
pub const T_CLOSE: u8 = 3;
pub const T_PING: u8 = 4;
pub const T_PONG: u8 = 5;

const WRITE_TIMEOUT: Duration = Duration::from_secs(10);

pub fn frame(t: u8, id: u32, payload: &[u8]) -> Vec<u8> {
    let mut v = Vec::with_capacity(5 + payload.len());
    v.push(t);
    v.extend_from_slice(&id.to_be_bytes());
    v.extend_from_slice(payload);
    v
}

pub fn parse(msg: &[u8]) -> Option<(u8, u32, &[u8])> {
    if msg.len() < 5 {
        return None;
    }
    let id = u32::from_be_bytes([msg[1], msg[2], msg[3], msg[4]]);
    Some((msg[0], id, &msg[5..]))
}

pub fn now() -> String {
    let d = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let s = d.as_secs() % 86400;
    format!("{:02}:{:02}:{:02}", s / 3600, (s % 3600) / 60, s % 60)
}

/// 打开后把每帧收发都打出来，排障用：设 MCB_DEBUG=1
pub fn dbg() -> bool {
    std::env::var("MCB_DEBUG").map(|v| v == "1").unwrap_or(false)
}

pub fn log(msg: &str) {
    println!("[{}] {}", now(), msg);
    let _ = std::io::stdout().flush();
}

/// 只写句柄。用 Arc 共享，避免每帧 dup 一个 fd。
pub type Sink = Arc<Mutex<WsStream>>;

pub fn send_frame_free(s: &Sink, t: u8, id: u32, payload: &[u8]) -> bool {
    let mut g = s.lock().unwrap_or_else(|e| e.into_inner());
    g.send_message(&frame(t, id, payload)).is_ok()
}

/// 裸发：兼容模式下玩家侧那条 WS 跑的**就是**原始 MC 字节流，
/// 不能再套隧道帧头，否则 websocat 那种老客户端会收到一坨乱码。
fn send_raw(s: &Sink, payload: &[u8]) -> bool {
    let mut g = s.lock().unwrap_or_else(|e| e.into_inner());
    g.send_message(payload).is_ok()
}

// ------------------------------------------------------------------ serve

pub struct ServeOpts {
    pub port: u16,
    pub tun_secret: String,
    pub fwd_token: String,
}

#[derive(Clone)]
enum Role {
    /// 服务端隧道（DSW）
    Tunnel(String),
    /// 好友转发器
    Forward(String),
    /// 兼容模式
    Legacy,
}

/// 从 URL path 判定角色。约定见文件头。
fn route(path: &str) -> Role {
    let segs: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
    if segs.len() == 4 && (segs[3] == "up" || segs[3] == "down") {
        match segs[0] {
            "t" => return Role::Tunnel(segs[2].to_string()),
            "f" => return Role::Forward(segs[2].to_string()),
            _ => {}
        }
    }
    Role::Legacy
}

/// 提取 path 里的密钥/令牌（第 2 段）。
fn route_secret(path: &str) -> Option<&str> {
    let segs: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
    if segs.len() == 4 {
        segs.get(1).copied()
    } else {
        None
    }
}

fn route_dir(path: &str) -> &str {
    let segs: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
    segs.last().copied().unwrap_or("")
}

/// 一条玩家侧连接的归属。
#[derive(Clone)]
struct Peer {
    session: String,
    /// 玩家侧只写句柄。好友转发器的 down 是**多条连接共享**的，
    /// 所以不能在这条连接结束时把它关掉（那会把好友的所有连接一起打死）。
    sink: Sink,
    /// 独占：这条 sink 只服务本连接（兼容模式的 websocat 路径）
    exclusive: bool,
}

struct Hub {
    /// CNB → DSW 的只写句柄
    tun_down: Mutex<Option<Sink>>,
    /// session → 好友转发器的 down 只写句柄
    fwd_down: Mutex<HashMap<String, Sink>>,
    peers: Mutex<HashMap<u32, Peer>>,
    next: AtomicU32,
    tun_up: AtomicU32,
}

impl Hub {
    fn tun_sink(&self) -> Option<Sink> {
        self.tun_down.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    fn peer(&self, id: u32) -> Option<Peer> {
        self.peers.lock().unwrap_or_else(|e| e.into_inner()).get(&id).cloned()
    }

    /// 收掉一条玩家侧连接。
    /// `notify_owner` = 还要告诉连接的主人（好友转发器）把本地 socket 关掉；
    /// 如果是主人自己发的 CLOSE，就别再回一份了。
    fn close_peer(&self, id: u32, notify_owner: bool) {
        let p = self.peers.lock().unwrap_or_else(|e| e.into_inner()).remove(&id);
        if let Some(p) = p {
            if p.exclusive {
                let _ = p.sink.lock().unwrap_or_else(|e| e.into_inner()).shutdown();
            } else if notify_owner {
                send_frame_free(&p.sink, T_CLOSE, id, &[]);
            }
        }
    }

    /// 某条好友链路整个断了：它名下的连接全部收掉，并通知服务端隧道。
    fn drop_session_peers(&self, session: &str) {
        let removed: Vec<u32> = {
            let mut g = self.peers.lock().unwrap_or_else(|e| e.into_inner());
            let ids: Vec<u32> = g.iter().filter(|(_, p)| p.session == session).map(|(k, _)| *k).collect();
            for id in &ids {
                g.remove(id);
            }
            ids
        };
        for id in removed {
            if let Some(s) = self.tun_sink() {
                send_frame_free(&s, T_CLOSE, id, &[]);
            }
        }
    }
}

pub fn serve(o: ServeOpts) -> Result<(), String> {
    let hub = Arc::new(Hub {
        tun_down: Mutex::new(None),
        fwd_down: Mutex::new(HashMap::new()),
        peers: Mutex::new(HashMap::new()),
        next: AtomicU32::new(1),
        tun_up: AtomicU32::new(0),
    });
    start_keepalive(hub.clone());

    let l = TcpListener::bind(("0.0.0.0", o.port)).map_err(|e| format!("监听 {} 失败: {e}", o.port))?;
    log(&format!("serve 已启动，监听 0.0.0.0:{}", o.port));
    log(&format!(
        "  /t/<secret>/<session>/up|down   服务端隧道   secret {}",
        if o.tun_secret.is_empty() { "未设置(不校验)" } else { "已设置" }
    ));
    log(&format!(
        "  /f/<token>/<session>/up|down    好友转发     token  {}",
        if o.fwd_token.is_empty() { "未设置(不校验)" } else { "已设置" }
    ));
    log("  /                               兼容模式（websocat 式一对一）");

    for conn in l.incoming() {
        let stream = match conn {
            Ok(s) => s,
            Err(e) => {
                log(&format!("accept 出错: {e}"));
                continue;
            }
        };
        let hub = hub.clone();
        let o = ServeOpts { port: o.port, tun_secret: o.tun_secret.clone(), fwd_token: o.fwd_token.clone() };
        thread::spawn(move || {
            let _ = handle_conn(stream, hub, o);
        });
    }
    Ok(())
}

fn start_keepalive(hub: Arc<Hub>) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(20));
        if hub.tun_up.load(Ordering::Relaxed) == 0 {
            continue;
        }
        if let Some(s) = hub.tun_sink() {
            send_frame_free(&s, T_PING, 0, &[]);
        }
        // 好友链路也要主动喂，不然他们在服务器列表页发呆时可能被中间代理判定空闲
        let sinks: Vec<Sink> =
            hub.fwd_down.lock().unwrap_or_else(|e| e.into_inner()).values().cloned().collect();
        for s in sinks {
            send_frame_free(&s, T_PING, 0, &[]);
        }
    });
}

/// 给浏览器看的一页链路状态：`https://<地址>/status`
///
/// 存在的意义：这条链有三段（玩家/好友端 → CNB 中继 → 魔搭 DSW → MC），
/// 出问题时最想知道的就是"断在哪一段"。这里由中继自己回答：
/// 魔搭那条反隧道在不在、有几个玩家/好友连着。
fn status_text(hub: &Hub) -> String {
    let tun_down = hub.tun_down.lock().unwrap_or_else(|e| e.into_inner()).is_some();
    let tun_up = hub.tun_up.load(Ordering::Relaxed) != 0;
    let peers = hub.peers.lock().unwrap_or_else(|e| e.into_inner()).len();
    let fwd = hub.fwd_down.lock().unwrap_or_else(|e| e.into_inner()).len();
    let dsw = if tun_up && tun_down {
        "✅ 已连接（这一段的管道是通的）"
    } else if tun_up || tun_down {
        "⚠️ 只有一半：上一次连接没断干净，客户端重连后通常会自愈"
    } else {
        "❌ 未连接 —— 魔搭那边的 mcbridge client 不在，或实例被回收了"
    };
    format!(
        "mcbridge 中继状态\n\
         ==============================\n\
         时间（本机）  : {}\n\
         魔搭 DSW 隧道 : {}\n\
         玩家/好友连接 : {} 条\n\
         好友转发会话  : {}\n\
         \n\
         说明：这是 CNB 上的中继自己报的。它只说明「中继到魔搭」这一段；\n\
         魔搭到 MC、以及 MC 本体是否正常，还要看玩家能不能进世界。\n",
        now(),
        dsw,
        peers,
        fwd
    )
}

fn handle_conn(stream: TcpStream, hub: Arc<Hub>, o: ServeOpts) -> Result<(), String> {
    let _ = stream.set_write_timeout(Some(WRITE_TIMEOUT));
    // 普通 HTTP GET /status 直接回一页链路状态（浏览器打开就能看「现在通不通」），
    // 其余非 WS 请求仍由 accept_with 回 426。
    let plain = |p: &str| -> Option<String> {
        if p == "/status" || p.starts_with("/status?") {
            Some(status_text(&hub))
        } else {
            None
        }
    };
    let ws = match WsStream::accept_with(stream, Some(&plain)) {
        Ok(w) => w,
        Err(e) if e.to_string().contains(ws::PLAIN_RESPONDED) => return Ok(()),
        Err(e) => return Err(format!("WS 握手失败: {e}")),
    };
    let path = ws.request_path.clone();
    let role = route(&path);

    let ok_auth = |want: &str| -> bool {
        let want = if want.is_empty() { NO_SECRET } else { want };
        match route_secret(&path) {
            Some(got) => got == want,
            None => false,
        }
    };

    match role {
        // ---------------- 服务端隧道（DSW） ----------------
        Role::Tunnel(session) => {
            if !ok_auth(&o.tun_secret) {
                log(&format!("拒绝：隧道密钥错误 path={path}"));
                return Err("密钥错误".into());
            }
            let dir = route_dir(&path).to_string();
            if dir == "down" {
                log(&format!("隧道 DOWN 接入 session={session}"));
                let sink: Sink = Arc::new(Mutex::new(ws));
                *hub.tun_down.lock().unwrap_or_else(|e| e.into_inner()) = Some(sink.clone());
                // 只写方向：仍然读，用来感知断开
                park_reader(sink.clone());
                let mut g = hub.tun_down.lock().unwrap_or_else(|e| e.into_inner());
                if g.as_ref().map(|s| Arc::ptr_eq(s, &sink)).unwrap_or(false) {
                    *g = None;
                }
                drop(g);
                log("隧道 DOWN 断开");
                return Ok(());
            }
            if dir == "up" {
                log(&format!("隧道 UP 接入 session={session}"));
                hub.tun_up.store(1, Ordering::Relaxed);
                tunnel_up_loop(ws, hub.clone());
                hub.tun_up.store(0, Ordering::Relaxed);
                log("隧道 UP 断开，回收全部玩家连接");
                let ids: Vec<u32> =
                    hub.peers.lock().unwrap_or_else(|e| e.into_inner()).keys().copied().collect();
                for id in ids {
                    hub.close_peer(id, true);
                }
                return Ok(());
            }
            Err(format!("未知方向: {dir}"))
        }

        // ---------------- 好友转发器 ----------------
        Role::Forward(session) => {
            if !ok_auth(&o.fwd_token) {
                log(&format!("拒绝：转发令牌错误 path={path}"));
                return Err("令牌错误".into());
            }
            let dir = route_dir(&path).to_string();
            if dir == "down" {
                log(&format!("好友转发 DOWN 接入 session={session}"));
                let sink: Sink = Arc::new(Mutex::new(ws));
                hub.fwd_down
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .insert(session.clone(), sink.clone());
                park_reader(sink);
                hub.fwd_down.lock().unwrap_or_else(|e| e.into_inner()).remove(&session);
                log(&format!("好友转发 DOWN 断开 session={session}"));
                hub.drop_session_peers(&session);
                return Ok(());
            }
            if dir == "up" {
                log(&format!("好友转发 UP 接入 session={session}"));
                forwarder_up_loop(ws, hub.clone(), session.clone());
                log(&format!("好友转发 UP 断开 session={session}"));
                hub.drop_session_peers(&session);
                return Ok(());
            }
            Err(format!("未知方向: {dir}"))
        }

        // ---------------- 兼容模式 ----------------
        Role::Legacy => legacy_player(ws, hub),
    }
}

/// 只写流：park 住一个读循环，用于感知断开。
fn park_reader(sink: Sink) {
    let mut r = {
        let g = sink.lock().unwrap_or_else(|e| e.into_inner());
        match g.try_clone() {
            Ok(c) => c,
            Err(_) => return,
        }
    };
    loop {
        match r.read_message() {
            Ok(Some(_)) => {}
            Ok(None) | Err(_) => break,
        }
    }
}

/// DSW 侧隧道：读 DATA/CLOSE，分发给玩家。
fn tunnel_up_loop(mut ws: WsStream, hub: Arc<Hub>) {
    loop {
        let m = match ws.read_message() {
            Ok(Some(m)) => m,
            Ok(None) => break,
            Err(_) => break,
        };
        let Some((t, id, p)) = parse(&m) else { continue };
        match t {
            T_DATA => match hub.peer(id) {
                Some(peer) => {
                    let ok = if peer.exclusive {
                        send_raw(&peer.sink, p)
                    } else {
                        send_frame_free(&peer.sink, T_DATA, id, p)
                    };
                    if dbg() {
                        log(&format!(
                            "[dbg] 隧道→玩家 id={id} {} 字节 exclusive={} 结果={}",
                            p.len(), peer.exclusive, ok
                        ));
                    }
                    if !ok {
                        // 玩家侧写不动了：收掉它，并让服务端隧道也把这条流放下
                        hub.close_peer(id, false);
                        if let Some(s) = hub.tun_sink() {
                            send_frame_free(&s, T_CLOSE, id, &[]);
                        }
                    }
                }
                None => {}
            },
            // 服务端那边把流关了：通知好友转发器关掉本地 socket
            T_CLOSE => hub.close_peer(id, true),
            T_PING => {
                if let Some(s) = hub.tun_sink() {
                    send_frame_free(&s, T_PONG, 0, &[]);
                }
            }
            _ => {}
        }
    }
}

/// 好友转发器：id 由转发器自己分配（高 16 位随机 session tag），这里纯转发。
fn forwarder_up_loop(mut ws: WsStream, hub: Arc<Hub>, session: String) {
    loop {
        let m = match ws.read_message() {
            Ok(Some(m)) => m,
            Ok(None) => break,
            Err(_) => break,
        };
        let Some((t, id, p)) = parse(&m) else { continue };
        match t {
            T_OPEN => {
                let sink = hub.fwd_down.lock().unwrap_or_else(|e| e.into_inner()).get(&session).cloned();
                match sink {
                    Some(s) => {
                        hub.peers.lock().unwrap_or_else(|e| e.into_inner()).insert(
                            id,
                            Peer { session: session.clone(), sink: s, exclusive: false },
                        );
                        if !hub.tun_sink().map(|s| send_frame_free(&s, T_OPEN, id, &[])).unwrap_or(false) {
                            hub.close_peer(id, true);
                            if let Some(s) = hub.tun_sink() {
                                send_frame_free(&s, T_CLOSE, id, &[]);
                            }
                        }
                    }
                    None => {
                        log(&format!("好友 UP 先于 DOWN 到达，丢弃 conn={id}"));
                    }
                }
            }
            T_DATA => {
                if let Some(s) = hub.tun_sink() {
                    send_frame_free(&s, T_DATA, id, p);
                }
            }
            T_PING => {
                if let Some(s) = hub.fwd_down.lock().unwrap_or_else(|e| e.into_inner()).get(&session).cloned() {
                    send_frame_free(&s, T_PONG, 0, &[]);
                }
            }
            // 好友本地那条连接断了：转发器自己知道，不用再通知它
            T_CLOSE => {
                hub.close_peer(id, false);
                if let Some(s) = hub.tun_sink() {
                    send_frame_free(&s, T_CLOSE, id, &[]);
                }
            }
            _ => {}
        }
    }
}

/// 兼容模式：一条双向 WS 直接对接服务端隧道的一条流。
fn legacy_player(ws: WsStream, hub: Arc<Hub>) -> Result<(), String> {
    let id = hub.next.fetch_add(1, Ordering::Relaxed);
    let w = ws.try_clone().map_err(|e| e.to_string())?;
    let sink: Sink = Arc::new(Mutex::new(w));
    hub.peers.lock().unwrap_or_else(|e| e.into_inner()).insert(
        id,
        Peer { session: "legacy".to_string(), sink, exclusive: true },
    );
    let open_ok = hub.tun_sink().map(|s| send_frame_free(&s, T_OPEN, id, &[])).unwrap_or(false);
    if !open_ok {
        hub.close_peer(id, false);
        return Err("服务端隧道未接入".into());
    }
    log(&format!("玩家接入（兼容模式）conn={id}"));

    let mut r = ws;
    loop {
        let m = match r.read_message() {
            Ok(Some(m)) => m,
            Ok(None) => break,
            Err(_) => break,
        };
        if dbg() { log(&format!("[dbg] 玩家→隧道 id={id} {} 字节", m.len())); }
        let ok = hub.tun_sink().map(|s| send_frame_free(&s, T_DATA, id, &m)).unwrap_or(false);
        if !ok {
            break;
        }
    }
    hub.close_peer(id, false);
    if let Some(s) = hub.tun_sink() {
        send_frame_free(&s, T_CLOSE, id, &[]);
    }
    log(&format!("玩家断开（兼容模式）conn={id}"));
    Ok(())
}

// ------------------------------------------------------------------ 拨号方共用

pub struct LinkOpts {
    pub host: String,
    pub port: u16,
    /// "/t/" 或 "/f/"
    pub prefix: String,
    pub secret: String,
    pub session: String,
    pub tls: bool,
}

/// 路径里空密钥用 `_` 占位，否则路径会塌成 3 段而被当成兼容模式。
const NO_SECRET: &str = "_";

pub fn link_paths(o: &LinkOpts) -> (String, String) {
    let sec = if o.secret.is_empty() { NO_SECRET.to_string() } else { o.secret.clone() };
    let base = format!("{}{}/{}/", o.prefix, sec, o.session);
    (format!("{base}up"), format!("{base}down"))
}

/// 建立 up（只写）/ down（只读）两条流。
pub fn connect_link(o: &LinkOpts) -> Result<(Sink, WsStream), String> {
    let (up_path, down_path) = link_paths(o);
    let up = WsStream::connect(&o.host, o.port, &up_path, o.tls)
        .map_err(|e| format!("UP 连接失败({up_path}): {e}"))?;
    let down = WsStream::connect(&o.host, o.port, &down_path, o.tls)
        .map_err(|e| format!("DOWN 连接失败({down_path}): {e}"))?;
    let _ = up.set_write_timeout(Some(WRITE_TIMEOUT));
    Ok((Arc::new(Mutex::new(up)), down))
}

/// 每 20 秒在 up 流上发一个 PING。
pub fn spawn_ping(up: Sink) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(20));
        if !send_frame_free(&up, T_PING, 0, &[]) {
            return;
        }
    });
}

pub fn random_session() -> String {
    let d = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|x| x.as_nanos() as u64)
        .unwrap_or(0);
    let mut seed = d ^ ((std::process::id() as u64) << 32);
    let mut out = String::new();
    for _ in 0..8 {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        out.push(char::from(b'a' + (seed % 26) as u8));
    }
    out
}

pub fn shutdown_all(s: &TcpStream) {
    let _ = s.shutdown(Shutdown::Both);
}
