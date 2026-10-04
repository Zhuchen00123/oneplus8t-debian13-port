//! 一个只够用的 HTTPS 客户端：用来自动拉取中继地址配置。
//! 只支持 GET + 明文响应体，够用就行，不为它引入一整个 HTTP 库。

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use crate::ws::Transport;

/// 把 "https://host[:port]/path" 拆开。
pub fn split_url(url: &str) -> Result<(bool, String, u16, String), String> {
    let (tls, rest) = if let Some(r) = url.strip_prefix("https://") {
        (true, r)
    } else if let Some(r) = url.strip_prefix("http://") {
        (false, r)
    } else {
        return Err("URL 必须以 http:// 或 https:// 开头".into());
    };
    let (hostport, path) = match rest.find('/') {
        Some(i) => (&rest[..i], &rest[i..]),
        None => (rest, "/"),
    };
    let (host, port) = match hostport.rsplit_once(':') {
        Some((h, p)) => (h.to_string(), p.parse::<u16>().map_err(|_| "端口不合法".to_string())?),
        None => (hostport.to_string(), if tls { 443 } else { 80 }),
    };
    if host.is_empty() {
        return Err("URL 缺少主机名".into());
    }
    Ok((tls, host, port, path.to_string()))
}

/// 抓一个文本文件。带 Host 头与 SNI，简单跟随一层重定向。
pub fn get_text(url: &str, timeout: Duration) -> Result<String, String> {
    let (tls, host, port, path) = split_url(url)?;
    let stream = TcpStream::connect((host.as_str(), port)).map_err(|e| format!("连接 {host}:{port} 失败: {e}"))?;
    let _ = stream.set_nodelay(true);
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));

    #[cfg(feature = "tls")]
    let mut t = if tls {
        let c = native_tls::TlsConnector::new().map_err(|e| format!("TLS 初始化失败: {e}"))?;
        Transport::Tls(Box::new(
            c.connect(&host, stream).map_err(|e| format!("TLS 握手失败: {e}"))?,
        ))
    } else {
        Transport::Plain(stream)
    };

    #[cfg(not(feature = "tls"))]
    let mut t = {
        if tls {
            return Err("此版本未编译 HTTPS 支持".into());
        }
        Transport::Plain(stream)
    };

    let req = format!(
        "GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: mcbridge\r\nAccept: */*\r\nConnection: close\r\n\r\n"
    );
    t.write_all(req.as_bytes()).map_err(|e| format!("发送请求失败: {e}"))?;
    t.flush().ok();

    let mut raw = Vec::new();
    let mut buf = [0u8; 8192];
    loop {
        match t.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                raw.extend_from_slice(&buf[..n]);
                if raw.len() > 256 * 1024 {
                    break;
                }
            }
            Err(e) => {
                if raw.is_empty() {
                    return Err(format!("读取响应失败: {e}"));
                }
                break;
            }
        }
    }

    let text = String::from_utf8_lossy(&raw).to_string();
    let head_end = text.find("\r\n\r\n").ok_or("响应格式异常")?;
    let head = &text[..head_end];
    let body = text[head_end + 4..].to_string();
    let status = head.lines().next().unwrap_or("");
    if !status.contains(" 200") {
        return Err(format!("HTTP {status}"));
    }
    // Transfer-Encoding: chunked —— 只做最简单的去壳
    if head.to_ascii_lowercase().contains("transfer-encoding: chunked") {
        return Ok(dechunk(&body));
    }
    Ok(body)
}

fn dechunk(body: &str) -> String {
    let mut out = String::new();
    let mut rest = body;
    loop {
        let Some(i) = rest.find("\r\n") else { break };
        let size_str = rest[..i].split(';').next().unwrap_or("").trim();
        let Ok(size) = usize::from_str_radix(size_str, 16) else { break };
        if size == 0 {
            break;
        }
        let start = i + 2;
        if start + size > rest.len() {
            out.push_str(&rest[start..]);
            break;
        }
        out.push_str(&rest[start..start + size]);
        rest = &rest[(start + size).min(rest.len())..];
        if rest.starts_with("\r\n") {
            rest = &rest[2..];
        }
    }
    out
}

/// 解析 `key=value` 形式的配置文本，忽略 `#` 注释与空行。
pub fn parse_kv(text: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = line.split_once('=') {
            out.push((k.trim().to_ascii_lowercase(), v.trim().to_string()));
        }
    }
    out
}
