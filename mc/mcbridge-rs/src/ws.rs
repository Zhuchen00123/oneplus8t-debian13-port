//! 极简 WebSocket 实现（纯 std）。
//!
//! 自己写而不用现成库有两个原因：
//!   1. 零依赖 → Linux 侧可以直接交叉编译成静态二进制，不需要任何工具链；
//!   2. 完全控制 TCP_NODELAY 与缓冲行为 —— MC 全是小包，这是延迟的关键。

use std::io::{Error, ErrorKind, Read, Result, Write};
use std::net::TcpStream;
use std::sync::{Arc, Mutex};

const MAX_MSG: usize = 16 * 1024 * 1024;

/// accept_with 用调用方给的内容直接回了一个普通 HTTP 响应（不是 WS），
/// 用这个当错误信息，调用方一眼能认出来、不当成故障记日志。
pub const PLAIN_RESPONDED: &str = "已按普通 HTTP 请求应答";

/// 同一 socket 上的多个 WsStream 副本共享同一把写锁，保证帧不会交错。
pub type WriteLock = Arc<Mutex<()>>;

// ---------------------------------------------------------------- base64 / sha1

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

pub fn b64_encode(data: &[u8]) -> String {
    let mut out = String::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        out.push(B64[(n >> 18) as usize & 63] as char);
        out.push(B64[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 { B64[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if chunk.len() > 2 { B64[n as usize & 63] as char } else { '=' });
    }
    out
}

pub fn sha1(data: &[u8]) -> [u8; 20] {
    let mut h: [u32; 5] = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0];
    let mut msg = data.to_vec();
    let bitlen = (data.len() as u64) * 8;
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bitlen.to_be_bytes());

    for block in msg.chunks(64) {
        let mut w = [0u32; 80];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([block[i * 4], block[i * 4 + 1], block[i * 4 + 2], block[i * 4 + 3]]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let (mut a, mut b, mut c, mut d, mut e) = (h[0], h[1], h[2], h[3], h[4]);
        for i in 0..80 {
            let (f, k) = match i {
                0..=19 => ((b & c) | ((!b) & d), 0x5A827999u32),
                20..=39 => (b ^ c ^ d, 0x6ED9EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1BBCDC),
                _ => (b ^ c ^ d, 0xCA62C1D6),
            };
            let tmp = a.rotate_left(5).wrapping_add(f).wrapping_add(e).wrapping_add(k).wrapping_add(w[i]);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = tmp;
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
    }
    let mut out = [0u8; 20];
    for i in 0..5 {
        out[i * 4..i * 4 + 4].copy_from_slice(&h[i].to_be_bytes());
    }
    out
}

// ---------------------------------------------------------------- 传输层

pub enum Transport {
    Plain(TcpStream),
    #[cfg(feature = "tls")]
    Tls(Box<native_tls::TlsStream<TcpStream>>),
}

impl Read for Transport {
    fn read(&mut self, buf: &mut [u8]) -> Result<usize> {
        match self {
            Transport::Plain(s) => s.read(buf),
            #[cfg(feature = "tls")]
            Transport::Tls(s) => s.read(buf),
        }
    }
}

impl Write for Transport {
    fn write(&mut self, buf: &[u8]) -> Result<usize> {
        match self {
            Transport::Plain(s) => s.write(buf),
            #[cfg(feature = "tls")]
            Transport::Tls(s) => s.write(buf),
        }
    }
    fn flush(&mut self) -> Result<()> {
        match self {
            Transport::Plain(s) => s.flush(),
            #[cfg(feature = "tls")]
            Transport::Tls(s) => s.flush(),
        }
    }
}

// ---------------------------------------------------------------- WS

pub struct WsStream {
    t: Transport,
    is_client: bool,
    lock: WriteLock,
    /// 服务端记录升级请求的 path（反隧道用它当共享密钥）
    pub request_path: String,
}

impl WsStream {
    fn new(t: Transport, is_client: bool, path: String, lock: WriteLock) -> WsStream {
        WsStream { t, is_client, lock, request_path: path }
    }

    /// 同一 socket 上再开一个句柄（读/写线程各用一个），共享写锁。
    /// 仅适用于明文 TCP —— 服务端一侧都是明文，够用。
    pub fn try_clone(&self) -> Result<WsStream> {
        match &self.t {
            Transport::Plain(s) => {
                let c = s.try_clone()?;
                let _ = c.set_nodelay(true);
                Ok(WsStream {
                    t: Transport::Plain(c),
                    is_client: self.is_client,
                    lock: self.lock.clone(),
                    request_path: self.request_path.clone(),
                })
            }
            #[cfg(feature = "tls")]
            Transport::Tls(_) => Err(Error::new(ErrorKind::Other, "TLS 流不支持拆分")),
        }
    }

    /// 服务端：从已建立的 TCP 连接完成 WS 握手。
    pub fn accept(stream: TcpStream) -> Result<WsStream> {
        WsStream::accept_with(stream, None)
    }

    /// `plain`：非 WebSocket 请求时，先让调用方看看路径要不要用自己的内容回一个 200
    /// （中继用它提供 /status 状态页）。回调返回 None 就走默认的 426。
    pub fn accept_with(
        stream: TcpStream,
        plain: Option<&dyn Fn(&str) -> Option<String>>,
    ) -> Result<WsStream> {
        let _ = stream.set_nodelay(true);
        let _ = stream.set_read_timeout(None);
        let mut ws = WsStream::new(
            Transport::Plain(stream),
            false,
            String::new(),
            Arc::new(Mutex::new(())),
        );
        let head = ws.read_http_head()?;
        let path = head
            .lines()
            .next()
            .and_then(|l| l.split(' ').nth(1))
            .unwrap_or("/")
            .to_string();
        ws.request_path = path.clone();

        let mut key = None;
        for line in head.lines() {
            if let Some((k, v)) = line.split_once(':') {
                if k.trim().eq_ignore_ascii_case("sec-websocket-key") {
                    key = Some(v.trim().to_string());
                }
            }
        }
        let key = match key {
            Some(k) => k,
            None => {
                if let Some(f) = plain {
                    if let Some(body) = f(&path) {
                        let resp = format!(
                            "HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                            body.as_bytes().len(),
                            body
                        );
                        let _ = ws.t.write_all(resp.as_bytes());
                        let _ = ws.t.flush();
                        return Err(Error::new(ErrorKind::ConnectionAborted, PLAIN_RESPONDED));
                    }
                }
                // 不是 WebSocket 升级请求 → 明确回一个 426 再断开。
                // 这样健康检查（以及排障的人）一眼就能分辨「端口后面是 mcbridge」
                // 还是「端口后面什么都没有」：后者只会拿到 CNB 代理的 500，
                // 两者长得一模一样（2026-09-18 被这个坑过，见 mc-cnb-watch.sh）。
                let body = "mcbridge: this port speaks WebSocket only\n";
                let resp = format!(
                    "HTTP/1.1 426 Upgrade Required\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = ws.t.write_all(resp.as_bytes());
                let _ = ws.t.flush();
                return Err(Error::new(
                    ErrorKind::InvalidData,
                    "不是 WebSocket 升级请求（已回 426）",
                ));
            }
        };
        let accept = b64_encode(&sha1(format!("{}258EAFA5-E914-47DA-95CA-C5AB0DC85B11", key).as_bytes()));
        let resp = format!(
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {}\r\n\r\n",
            accept
        );
        ws.t.write_all(resp.as_bytes())?;
        ws.t.flush()?;
        Ok(ws)
    }

    /// 客户端：连上 host:port 并发起 WS 升级。`use_tls` 决定走 wss。
    pub fn connect(host: &str, port: u16, path: &str, use_tls: bool) -> Result<WsStream> {
        let stream = TcpStream::connect((host, port))?;
        let _ = stream.set_nodelay(true);

        #[cfg(feature = "tls")]
        let transport = if use_tls {
            let connector = native_tls::TlsConnector::new()
                .map_err(|e| Error::new(ErrorKind::Other, format!("TLS 初始化失败: {e}")))?;
            let tls = connector
                .connect(host, stream)
                .map_err(|e| Error::new(ErrorKind::Other, format!("TLS 握手失败: {e}")))?;
            Transport::Tls(Box::new(tls))
        } else {
            Transport::Plain(stream)
        };

        #[cfg(not(feature = "tls"))]
        let transport = {
            if use_tls {
                return Err(Error::new(ErrorKind::Other, "此二进制未编译 TLS 支持"));
            }
            Transport::Plain(stream)
        };

        let key = b64_encode(&{
            // 16 字节伪随机 nonce
            let mut seed = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u64)
                .unwrap_or(0)
                ^ (std::process::id() as u64) << 32;
            let mut b = [0u8; 16];
            for x in b.iter_mut() {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                *x = seed as u8;
            }
            b
        });

        let mut ws = WsStream::new(transport, true, path.to_string(), Arc::new(Mutex::new(())));
        let req = format!(
            "GET {} HTTP/1.1\r\nHost: {}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {}\r\nSec-WebSocket-Version: 13\r\n\r\n",
            path, host, key
        );
        ws.t.write_all(req.as_bytes())?;
        ws.t.flush()?;
        let head = ws.read_http_head()?;
        if !head.starts_with("HTTP/1.1 101") {
            return Err(Error::new(ErrorKind::Other, format!("WS 升级失败: {}", head.lines().next().unwrap_or(""))));
        }
        Ok(ws)
    }

    fn read_http_head(&mut self) -> Result<String> {
        let mut buf = Vec::with_capacity(1024);
        let mut one = [0u8; 1];
        while buf.len() < 16 * 1024 {
            let n = self.t.read(&mut one)?;
            if n == 0 {
                return Err(Error::new(ErrorKind::UnexpectedEof, "握手期间连接关闭"));
            }
            buf.push(one[0]);
            let c = buf.len();
            if c >= 4 && &buf[c - 4..] == b"\r\n\r\n" {
                break;
            }
        }
        String::from_utf8(buf).map_err(|_| Error::new(ErrorKind::InvalidData, "握手头非 UTF-8"))
    }

    fn read_exact_vec(&mut self, n: usize) -> Result<Vec<u8>> {
        let mut v = vec![0u8; n];
        self.t.read_exact(&mut v)?;
        Ok(v)
    }

    /// 读一条完整的二进制消息（自动处理分片、ping/pong）。`Ok(None)` = 对端关闭。
    pub fn read_message(&mut self) -> Result<Option<Vec<u8>>> {
        let mut acc: Option<Vec<u8>> = None;
        loop {
            let h = self.read_exact_vec(2)?;
            let fin = h[0] & 0x80 != 0;
            let opcode = h[0] & 0x0F;
            let masked = h[1] & 0x80 != 0;
            let mut len = (h[1] & 0x7F) as u64;

            if len == 126 {
                let e = self.read_exact_vec(2)?;
                len = u16::from_be_bytes([e[0], e[1]]) as u64;
            } else if len == 127 {
                let e = self.read_exact_vec(8)?;
                len = u64::from_be_bytes(e.try_into().unwrap());
            }
            if len > MAX_MSG as u64 {
                return Err(Error::new(ErrorKind::InvalidData, "帧过大"));
            }

            let mask = if masked { Some(self.read_exact_vec(4)?) } else { None };
            let mut payload = self.read_exact_vec(len as usize)?;
            if let Some(m) = mask {
                for (i, b) in payload.iter_mut().enumerate() {
                    *b ^= m[i & 3];
                }
            }

            match opcode {
                0x8 => {
                    let _ = self.send_control(0x8, &[]);
                    return Ok(None);
                }
                0x9 => {
                    let _ = self.send_control(0xA, &payload);
                    continue;
                }
                0xA => continue,
                0x0 => {
                    let a = acc.as_mut().ok_or_else(|| Error::new(ErrorKind::InvalidData, "意外的续帧"))?;
                    a.extend_from_slice(&payload);
                    if fin {
                        return Ok(acc);
                    }
                    continue;
                }
                0x1 | 0x2 => {
                    if fin {
                        return Ok(Some(payload));
                    }
                    let mut a = Vec::with_capacity(payload.len());
                    a.extend_from_slice(&payload);
                    acc = Some(a);
                    continue;
                }
                _ => return Err(Error::new(ErrorKind::InvalidData, "不支持的 opcode")),
            }
        }
    }

    pub fn send_message(&mut self, payload: &[u8]) -> Result<()> {
        self.send_frame(0x2, payload)
    }

    fn send_control(&mut self, opcode: u8, payload: &[u8]) -> Result<()> {
        self.send_frame(opcode, payload)
    }

    fn send_frame(&mut self, opcode: u8, payload: &[u8]) -> Result<()> {
        let n = payload.len();
        let mut frame = Vec::with_capacity(n + 14);
        frame.push(0x80 | opcode);
        let mask_bit = if self.is_client { 0x80 } else { 0x00 };
        if n < 126 {
            frame.push(mask_bit | n as u8);
        } else if n <= u16::MAX as usize {
            frame.push(mask_bit | 126);
            frame.extend_from_slice(&(n as u16).to_be_bytes());
        } else {
            frame.push(mask_bit | 127);
            frame.extend_from_slice(&(n as u64).to_be_bytes());
        }
        if self.is_client {
            let mut seed = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u64)
                .unwrap_or(0)
                ^ ((self as *const _ as u64) << 16);
            let mut m = [0u8; 4];
            for x in m.iter_mut() {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                *x = seed as u8;
            }
            frame.extend_from_slice(&m);
            for (i, b) in payload.iter().enumerate() {
                frame.push(b ^ m[i & 3]);
            }
        } else {
            frame.extend_from_slice(payload);
        }
        let _guard = self.lock.lock().unwrap_or_else(|e| e.into_inner());
        self.t.write_all(&frame)?;
        self.t.flush()
    }

    pub fn shutdown(&mut self) {
        let _ = self.send_control(0x8, &[]);
        let _ = self.t.flush();
    }

    pub fn set_write_timeout(&self, d: Option<std::time::Duration>) -> Result<()> {
        match &self.t {
            Transport::Plain(s) => s.set_write_timeout(d),
            #[cfg(feature = "tls")]
            Transport::Tls(s) => s.get_ref().set_write_timeout(d),
        }
    }

    pub fn set_read_timeout(&self, d: Option<std::time::Duration>) -> Result<()> {
        match &self.t {
            Transport::Plain(s) => s.set_read_timeout(d),
            #[cfg(feature = "tls")]
            Transport::Tls(s) => s.get_ref().set_read_timeout(d),
        }
    }
}
