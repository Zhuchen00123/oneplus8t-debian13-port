//! 两个拨号方：
//!
//!   client  —— 跑在魔搭 DSW 上。收到 OPEN 就去连本机 MC，把 MC 的字节回灌隧道。
//!   forward —— 跑在好友的 Windows 上。本机开一个 MC 端口，谁连上来就开一条隧道流。
//!
//! 两者都是「up 只写 / down 只读」，所以不需要在阻塞读的流上加锁。

use std::collections::HashMap;
use std::io::{ErrorKind, Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crate::tunnel::{
    connect_link, log, parse, random_session, send_frame_free, spawn_ping, LinkOpts, T_CLOSE, T_DATA,
    T_OPEN, T_PING, T_PONG,
};

/// 打开后会把每条流的收发都打到 stderr，排障用：设 MCB_DEBUG=1
fn dbg() -> bool {
    std::env::var("MCB_DEBUG").map(|v| v == "1").unwrap_or(false)
}

fn is_disconnect(e: &std::io::Error) -> bool {
    matches!(
        e.kind(),
        ErrorKind::WouldBlock | ErrorKind::TimedOut | ErrorKind::Interrupted
    )
}

// ================================================================ client (DSW)

pub struct ClientOpts {
    pub host: String,
    pub port: u16,
    pub secret: String,
    pub tls: bool,
    /// 本机 MC 地址
    pub mc: String,
    pub session: String,
    /// 断线后自动重连
    pub reconnect: bool,
}

pub fn client(o: ClientOpts) -> Result<(), String> {
    loop {
        match client_once(&o) {
            Ok(()) => log("隧道已断开"),
            Err(e) => log(&format!("隧道错误: {e}")),
        }
        if !o.reconnect {
            return Ok(());
        }
        log("5 秒后重连…");
        thread::sleep(Duration::from_secs(5));
    }
}

fn client_once(o: &ClientOpts) -> Result<(), String> {
    let lopts = LinkOpts {
        host: o.host.clone(),
        port: o.port,
        prefix: "/t/".into(),
        secret: o.secret.clone(),
        session: o.session.clone(),
        tls: o.tls,
    };
    let (up, mut down) = connect_link(&lopts)?;
    log(&format!(
        "已连上 CNB {}:{}  转发到本机 {}",
        o.host, o.port, o.mc
    ));
    spawn_ping(up.clone());

    // connId → MC 侧 socket（写句柄）
    let conns: Arc<Mutex<HashMap<u32, TcpStream>>> = Arc::new(Mutex::new(HashMap::new()));

    let result = loop {
        let m = match down.read_message() {
            Ok(Some(m)) => m,
            Ok(None) => break Ok(()),
            Err(e) => break Err(format!("DOWN 读失败: {e}")),
        };
        let Some((t, id, p)) = parse(&m) else { continue };
        match t {
            T_OPEN => {
                if dbg() { log(&format!("[dbg] OPEN id={id} → 连 {}", o.mc)); }
                match TcpStream::connect(&o.mc) {
                    Ok(mc) => {
                        let _ = mc.set_nodelay(true);
                        mc.set_read_timeout(Some(Duration::from_millis(500))).ok();
                        let w = match mc.try_clone() {
                            Ok(w) => w,
                            Err(e) => {
                                log(&format!("clone 失败: {e}"));
                                send_frame_free(&up, T_CLOSE, id, &[]);
                                continue;
                            }
                        };
                        conns.lock().unwrap_or_else(|e| e.into_inner()).insert(id, w);
                        let u = up.clone();
                        let c = conns.clone();
                        thread::spawn(move || {
                            let mut r = mc;
                            let mut buf = vec![0u8; 16384];
                            loop {
                                match r.read(&mut buf) {
                                    Ok(0) => break,
                                    Ok(n) => {
                                        if dbg() { log(&format!("[dbg] MC→隧道 id={id} {n} 字节")); }
                                        if !send_frame_free(&u, T_DATA, id, &buf[..n]) {
                                            break;
                                        }
                                    }
                                    Err(ref e) if is_disconnect(e) => continue,
                                    Err(_) => break,
                                }
                            }
                            if dbg() { log(&format!("[dbg] MC 侧读结束 id={id}，发 CLOSE")); }
                            c.lock().unwrap_or_else(|e| e.into_inner()).remove(&id);
                            send_frame_free(&u, T_CLOSE, id, &[]);
                        });
                    }
                    Err(e) => {
                        log(&format!("连本机 MC {} 失败: {e}", o.mc));
                        send_frame_free(&up, T_CLOSE, id, &[]);
                    }
                }
            }
            T_DATA => {
                if dbg() { log(&format!("[dbg] 隧道→MC id={id} {} 字节  有连接={}", p.len(), conns.lock().unwrap_or_else(|e| e.into_inner()).contains_key(&id))); }
                let w = conns.lock().unwrap_or_else(|e| e.into_inner()).get(&id).and_then(|s| s.try_clone().ok());
                if let Some(mut w) = w {
                    if w.write_all(p).is_err() {
                        conns.lock().unwrap_or_else(|e| e.into_inner()).remove(&id);
                        send_frame_free(&up, T_CLOSE, id, &[]);
                    }
                }
            }
            T_CLOSE => {
                if let Some(s) = conns.lock().unwrap_or_else(|e| e.into_inner()).remove(&id) {
                    let _ = s.shutdown(Shutdown::Both);
                }
            }
            T_PING => {
                send_frame_free(&up, T_PONG, 0, &[]);
            }
            _ => {}
        }
    };

    // 收尾
    let all: Vec<u32> = conns.lock().unwrap_or_else(|e| e.into_inner()).keys().copied().collect();
    for id in all {
        if let Some(s) = conns.lock().unwrap_or_else(|e| e.into_inner()).remove(&id) {
            let _ = s.shutdown(Shutdown::Both);
        }
    }
    result
}

// ================================================================ forward (好友)

#[derive(Clone)]
pub struct ForwardOpts {
    pub host: String,
    pub port: u16,
    pub token: String,
    pub tls: bool,
    /// 本机监听地址，形如 127.0.0.1:25565
    pub listen: String,
}

/// 好友侧转发。阻塞运行，直到 `stop` 被置位。
pub fn forward(
    o: ForwardOpts,
    stop: &'static AtomicBool,
    status: Arc<dyn Fn(&str) + Send + Sync>,
) -> Result<(), String> {

    // 会话 tag：高 16 位放进 connId，多台客户端同时在线也不会撞号
    let tag: u32 = {
        let s = random_session();
        let mut v: u32 = 0;
        for b in s.bytes() {
            v = v.wrapping_mul(31).wrapping_add(b as u32);
        }
        v & 0xFFFF
    };

    // 先占本地端口，再去连中继。反过来的话端口被占用时会先建立一条中继连接、
    // 再报错退出，那条连接和它的心跳线程就漏在外面了。
    let listener = TcpListener::bind(&o.listen).map_err(|e| {
        if e.kind() == ErrorKind::AddrInUse {
            format!(
                "本地 {} 已被别的程序占用——多半是旧的 websocat 或另一个 mcbridge 还开着，或者你自己开过局域网世界。把它关掉，这里会自动接上。",
                o.listen
            )
        } else {
            format!("监听 {} 失败: {e}", o.listen)
        }
    })?;

    let lopts = LinkOpts {
        host: o.host.clone(),
        port: o.port,
        prefix: "/f/".into(),
        secret: o.token.clone(),
        session: random_session(),
        tls: o.tls,
    };
    let (up, mut down) = connect_link(&lopts)?;
    log(&format!("已连上中继 {}:{}", o.host, o.port));
    spawn_ping(up.clone());
    log(&format!("本机监听 {}", o.listen));
    status(&format!("已连接 · 游戏内填 {}", o.listen));
    listener.set_nonblocking(true).map_err(|e| e.to_string())?;

    // connId → 本机 MC 客户端 socket 写句柄
    let socks: Arc<Mutex<HashMap<u32, TcpStream>>> = Arc::new(Mutex::new(HashMap::new()));
    let next = Arc::new(AtomicU32::new(1));

    // 接客线程
    {
        let socks = socks.clone();
        let up = up.clone();
        let listener = listener.try_clone().map_err(|e| e.to_string())?;
        let status = status.clone();
        thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                match listener.accept() {
                    Ok((s, _addr)) => {
                        let _ = s.set_nodelay(true);
                        let _ = s.set_read_timeout(Some(Duration::from_millis(500)));
                        let id = (tag << 16) | (next.fetch_add(1, Ordering::Relaxed) & 0xFFFF);
                        let w = match s.try_clone() {
                            Ok(w) => w,
                            Err(_) => continue,
                        };
                        socks.lock().unwrap_or_else(|e| e.into_inner()).insert(id, w);
                        if !send_frame_free(&up, T_OPEN, id, &[]) {
                            socks.lock().unwrap_or_else(|e| e.into_inner()).remove(&id);
                            status("连接已断开");
                            continue;
                        }
                        log(&format!("本机 MC 客户端接入 id={id}"));
                        let up2 = up.clone();
                        let socks2 = socks.clone();
                        thread::spawn(move || {
                            let mut r = s;
                            let mut buf = vec![0u8; 16384];
                            loop {
                                match r.read(&mut buf) {
                                    Ok(0) => break,
                                    Ok(n) => {
                                        if !send_frame_free(&up2, T_DATA, id, &buf[..n]) {
                                            break;
                                        }
                                    }
                                    Err(ref e) if is_disconnect(e) => continue,
                                    Err(_) => break,
                                }
                            }
                            socks2.lock().unwrap_or_else(|e| e.into_inner()).remove(&id);
                            send_frame_free(&up2, T_CLOSE, id, &[]);
                            log(&format!("本机 MC 客户端断开 id={id}"));
                        });
                    }
                    Err(ref e) if e.kind() == ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(50));
                    }
                    Err(_) => thread::sleep(Duration::from_millis(200)),
                }
            }
        });
    }

    // 下行线程（本线程）
    let result = loop {
        if stop.load(Ordering::Relaxed) {
            break Ok(());
        }
        let m = match down.read_message() {
            Ok(Some(m)) => m,
            Ok(None) => break Err("中继连接已关闭".into()),
            Err(e) => break Err(format!("读中继失败: {e}")),
        };
        let Some((t, id, p)) = parse(&m) else { continue };
        match t {
            T_DATA => {
                let w = socks.lock().unwrap_or_else(|e| e.into_inner()).get(&id).and_then(|s| s.try_clone().ok());
                if let Some(mut w) = w {
                    if w.write_all(p).is_err() {
                        socks.lock().unwrap_or_else(|e| e.into_inner()).remove(&id);
                        send_frame_free(&up, T_CLOSE, id, &[]);
                    }
                }
            }
            T_CLOSE => {
                if let Some(s) = socks.lock().unwrap_or_else(|e| e.into_inner()).remove(&id) {
                    let _ = s.shutdown(Shutdown::Both);
                }
            }
            T_PING => {
                send_frame_free(&up, T_PONG, 0, &[]);
            }
            _ => {}
        }
    };

    for (_, s) in socks.lock().unwrap_or_else(|e| e.into_inner()).drain() {
        let _ = s.shutdown(Shutdown::Both);
    }
    status("已断开");
    result
}
