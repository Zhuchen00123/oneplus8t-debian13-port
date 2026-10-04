//! mcbridge —— 一个自己写的 Minecraft 反隧道直连工具。
//!
//! 三种模式：
//!   serve    跑在 CNB 上。单进程同时扮演「玩家入口的 WS 网关」和「反隧道服务端」，
//!            把原来的 websocat + wstunnel server 两个进程合成一个，少一层拆装。
//!   client   跑在魔搭 DSW 上。连 CNB，收到请求就去连本机 MC。
//!   forward  跑在好友的 Windows 上（默认带界面）。本机开 25565，游戏直连即可。
//!
//! 另外还有 fetch，只用来手动验证中继地址配置能不能拉到。

mod dialer;
mod http;
mod tunnel;
mod ws;

#[cfg(windows)]
mod gui;

use std::collections::HashMap;
use std::sync::atomic::AtomicBool;

use dialer::{client, forward, ClientOpts, ForwardOpts};
use tunnel::ServeOpts;

/// 自动获取地址的位置。注意 CNB 的公开原始文件接口是 `/-/git/raw/<ref>/<path>`，
/// `/-/raw/...` 那个会返回网页而不是文件。
const DEFAULT_CONFIG_URL: &str =
    "https://cnb.cool/zc354/mmmmcccccbuild/-/git/raw/main/endpoint.txt";

/// 内置的兜底地址：网络拉不到时直接用这个，好友照样能连。
/// 换环境后这个值会过期，但拉取成功时会被覆盖，所以不用重发 exe。
const DEFAULT_ENDPOINT: &str = "8tc9q7rmqa-9000.cnb.run:443/fwd_2b7e91c4d6a80f53";

fn parse_args(args: &[String]) -> HashMap<String, String> {
    let mut m = HashMap::new();
    let mut i = 0;
    while i < args.len() {
        let a = &args[i];
        if let Some(k) = a.strip_prefix("--") {
            let (k, v) = match k.split_once('=') {
                Some((k, v)) => (k.to_string(), v.to_string()),
                None => {
                    let v = args.get(i + 1).filter(|s| !s.starts_with("--")).cloned();
                    match v {
                        Some(v) => {
                            i += 1;
                            (k.to_string(), v)
                        }
                        None => (k.to_string(), "true".to_string()),
                    }
                }
            };
            m.insert(k.replace('-', "_"), v);
        }
        i += 1;
    }
    m
}

fn flag(m: &HashMap<String, String>, k: &str) -> bool {
    matches!(m.get(k).map(|s| s.as_str()), Some("true") | Some("1") | Some("yes"))
}

fn get<'a>(m: &'a HashMap<String, String>, k: &str, d: &'a str) -> String {
    m.get(k).cloned().unwrap_or_else(|| d.to_string())
}

fn u16_of(m: &HashMap<String, String>, k: &str, d: u16) -> u16 {
    m.get(k).and_then(|s| s.parse().ok()).unwrap_or(d)
}

fn usage() {
    println!(
        r#"mcbridge —— Minecraft 反隧道直连工具

用法：
  mcbridge serve   --port <监听端口> [--tun-secret <密钥>] [--fwd-token <令牌>]
  mcbridge client  --host <中继主机> --port <端口> [--secret <密钥>] [--mc 127.0.0.1:25565]
                   [--session <名字>] [--plain] [--once]
  mcbridge forward --host <中继主机> --port <端口> [--token <令牌>]
                   [--listen 127.0.0.1:25565] [--plain] [--no-gui]
  mcbridge fetch   [--url <配置地址>]

不带参数直接运行 = 打开小窗口（Windows）。"#
    );
}

/// 读取 exe 同目录的 mcbridge.ini（可选），可以覆盖配置地址和内置地址。
///   config_url=<拉到端点的地址>      endpoint=<直接写死的中继地址>
fn read_ini() -> Vec<(String, String)> {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let ini = dir.join("mcbridge.ini");
            if let Ok(text) = std::fs::read_to_string(&ini) {
                return http::parse_kv(&text);
            }
        }
    }
    Vec::new()
}

fn config_url(ini: &[(String, String)]) -> String {
    ini.iter()
        .find(|(k, _)| k == "config_url")
        .map(|(_, v)| v.clone())
        .unwrap_or_else(|| DEFAULT_CONFIG_URL.to_string())
}

fn baked_endpoint(ini: &[(String, String)]) -> String {
    ini.iter()
        .find(|(k, _)| k == "endpoint")
        .map(|(_, v)| v.clone())
        .unwrap_or_else(|| DEFAULT_ENDPOINT.to_string())
}

/// 重新去配置地址拉一次中继地址。界面在反复连不上时会调它，
/// 用来发现「CNB 环境重建导致地址变了」这件事。
pub(crate) fn refresh_endpoint() -> Option<String> {
    let ini = read_ini();
    fetch_endpoint(&config_url(&ini)).ok()
}

/// 拉取 endpoint=host:port/token 形式的配置。返回 "host:port/token"。
fn fetch_endpoint(url: &str) -> Result<String, String> {
    let text = http::get_text(url, std::time::Duration::from_secs(6))?;
    for (k, v) in http::parse_kv(&text) {
        if k == "endpoint" {
            return Ok(v);
        }
    }
    let t = text.trim();
    if t.is_empty() {
        return Err("配置内容为空".into());
    }
    if t.contains(':') && !t.contains('=') && t.len() < 200 {
        return Ok(t.to_string());
    }
    Err("配置里没有 endpoint= 这一项".into())
}

fn main() {
    let argv: Vec<String> = std::env::args().collect();
    let mode = argv.get(1).map(|s| s.as_str()).unwrap_or("");
    let a = parse_args(&argv[2.min(argv.len())..]);

    match mode {
        "serve" => {
            let o = ServeOpts {
                port: u16_of(&a, "port", 9000),
                tun_secret: get(&a, "tun_secret", ""),
                fwd_token: get(&a, "fwd_token", ""),
            };
            if let Err(e) = tunnel::serve(o) {
                tunnel::log(&format!("serve 退出: {e}"));
                std::process::exit(1);
            }
        }

        "client" => {
            let host = get(&a, "host", "");
            if host.is_empty() {
                eprintln!("缺少 --host");
                std::process::exit(2);
            }
            let o = ClientOpts {
                host,
                port: u16_of(&a, "port", 7000),
                secret: get(&a, "secret", ""),
                tls: !flag(&a, "plain"),
                mc: get(&a, "mc", "127.0.0.1:25565"),
                session: get(&a, "session", "dsw"),
                reconnect: !flag(&a, "once"),
            };
            if let Err(e) = client(o) {
                tunnel::log(&format!("client 退出: {e}"));
                std::process::exit(1);
            }
        }

        "forward" => {
            let host = get(&a, "host", "");
            if host.is_empty() {
                eprintln!("缺少 --host");
                std::process::exit(2);
            }
            let listen = get(&a, "listen", "127.0.0.1:25565");

            #[cfg(windows)]
            if !flag(&a, "no_gui") {
                let hint = format!("{}:{}/{}", host, u16_of(&a, "port", 9000), get(&a, "token", ""));
                std::process::exit(gui::run(hint));
            }

            static STOP: AtomicBool = AtomicBool::new(false);
            let o = ForwardOpts {
                host,
                port: u16_of(&a, "port", 9000),
                token: get(&a, "token", ""),
                tls: !flag(&a, "plain"),
                listen,
            };
            let cb = std::sync::Arc::new(|s: &str| tunnel::log(s));
            if let Err(e) = forward(o, &STOP, cb) {
                tunnel::log(&format!("forward 退出: {e}"));
                std::process::exit(1);
            }
        }

        "fetch" => {
            let ini = read_ini();
            let url = get(&a, "url", &config_url(&ini));
            match fetch_endpoint(&url) {
                Ok(v) => println!("OK  {v}"),
                Err(e) => {
                    println!("失败  {e}");
                    std::process::exit(1);
                }
            }
        }

        _ => {
            #[cfg(windows)]
            {
                let ini = read_ini();
                let url = config_url(&ini);
                let baked = baked_endpoint(&ini);
                let has_baked = !baked.is_empty();
                // 后台拉最新地址，窗口先带着内置地址显示出来
                std::thread::spawn(move || match fetch_endpoint(&url) {
                    Ok(v) => {
                        println!("自动获取到中继地址: {v}");
                        gui::set_endpoint_and_connect(v);
                    }
                    Err(e) => {
                        println!("自动获取地址失败: {e}");
                        if has_baked {
                            gui::set_status("没连上更新服务器，先用内置地址试");
                        } else {
                            gui::set_status("自动获取失败，请手动填写中继地址");
                        }
                        // 拿不到新地址也要连——内置的那个往往还是好的
                        gui::auto_connect();
                    }
                });
                std::process::exit(gui::run(baked));
            }
            #[cfg(not(windows))]
            {
                usage();
                std::process::exit(1);
            }
        }
    }
}
