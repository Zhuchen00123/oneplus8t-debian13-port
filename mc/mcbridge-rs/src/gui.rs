//! 极简 Win32 界面（纯 FFI，不依赖任何 crate）。
//!
//! 界面上只有：中继地址输入框、连接/断开两个按钮、状态文字、复制游戏地址。
//! 启动时自动从中继地址拉取配置，失败就提示手动填写 —— 满足“开箱即用”。

#![cfg(windows)]
#![allow(non_snake_case, non_camel_case_types, dead_code)]

use std::sync::atomic::{AtomicBool, AtomicIsize, Ordering};
use std::sync::Arc;
use std::thread;

use crate::dialer::{forward, ForwardOpts};
use crate::tunnel::log;

type HWND = *mut core::ffi::c_void;
type HINSTANCE = *mut core::ffi::c_void;
type HMENU = *mut core::ffi::c_void;
type HICON = *mut core::ffi::c_void;
type HCURSOR = *mut core::ffi::c_void;
type HBRUSH = *mut core::ffi::c_void;
type HGDIOBJ = *mut core::ffi::c_void;
type LPCWSTR = *const u16;
type UINT = u32;
type WPARAM = usize;
type LPARAM = isize;
type LRESULT = isize;

#[repr(C)]
struct WNDCLASSW {
    style: UINT,
    lpfnWndProc: Option<unsafe extern "system" fn(HWND, UINT, WPARAM, LPARAM) -> LRESULT>,
    cbClsExtra: i32,
    cbWndExtra: i32,
    hInstance: HINSTANCE,
    hIcon: HICON,
    hCursor: HCURSOR,
    hbrBackground: HBRUSH,
    lpszMenuName: LPCWSTR,
    lpszClassName: LPCWSTR,
}

#[repr(C)]
struct POINT {
    x: i32,
    y: i32,
}

#[repr(C)]
struct MSG {
    hwnd: HWND,
    message: UINT,
    wParam: WPARAM,
    lParam: LPARAM,
    time: u32,
    pt: POINT,
}

#[link(name = "user32")]
extern "system" {
    fn RegisterClassW(c: *const WNDCLASSW) -> u16;
    fn CreateWindowExW(
        ex: u32,
        class: LPCWSTR,
        title: LPCWSTR,
        style: u32,
        x: i32,
        y: i32,
        w: i32,
        h: i32,
        parent: HWND,
        menu: HMENU,
        inst: HINSTANCE,
        param: *mut core::ffi::c_void,
    ) -> HWND;
    fn DefWindowProcW(h: HWND, m: UINT, w: WPARAM, l: LPARAM) -> LRESULT;
    fn ShowWindow(h: HWND, cmd: i32) -> i32;
    fn UpdateWindow(h: HWND) -> i32;
    fn GetMessageW(m: *mut MSG, h: HWND, min: UINT, max: UINT) -> i32;
    fn TranslateMessage(m: *const MSG) -> i32;
    fn DispatchMessageW(m: *const MSG) -> LRESULT;
    fn PostQuitMessage(code: i32);
    fn DestroyWindow(h: HWND) -> i32;
    fn SetWindowTextW(h: HWND, s: LPCWSTR) -> i32;
    fn GetWindowTextW(h: HWND, s: *mut u16, max: i32) -> i32;
    fn GetWindowTextLengthW(h: HWND) -> i32;
    fn SendMessageW(h: HWND, m: UINT, w: WPARAM, l: LPARAM) -> LRESULT;
    fn EnableWindow(h: HWND, enable: i32) -> i32;
    fn PostMessageW(h: HWND, m: UINT, w: WPARAM, l: LPARAM) -> i32;
    fn MessageBoxW(h: HWND, text: LPCWSTR, cap: LPCWSTR, flags: UINT) -> i32;
    fn LoadCursorW(inst: HINSTANCE, name: LPCWSTR) -> HCURSOR;
    fn OpenClipboard(h: HWND) -> i32;
    fn EmptyClipboard() -> i32;
    fn SetClipboardData(fmt: UINT, mem: *mut core::ffi::c_void) -> *mut core::ffi::c_void;
    fn CloseClipboard() -> i32;
    fn SetForegroundWindow(h: HWND) -> i32;
}

#[link(name = "gdi32")]
extern "system" {
    fn CreateFontW(
        h: i32,
        w: i32,
        esc: i32,
        orient: i32,
        weight: i32,
        italic: u32,
        underline: u32,
        strike: u32,
        charset: u32,
        prec: u32,
        clip: u32,
        quality: u32,
        pitch: u32,
        face: LPCWSTR,
    ) -> HGDIOBJ;
}

#[link(name = "kernel32")]
extern "system" {
    fn GetModuleHandleW(name: LPCWSTR) -> HINSTANCE;
    fn GlobalAlloc(flags: UINT, bytes: usize) -> *mut core::ffi::c_void;
    fn GlobalLock(h: *mut core::ffi::c_void) -> *mut core::ffi::c_void;
    fn GlobalUnlock(h: *mut core::ffi::c_void) -> i32;
}

const WS_OVERLAPPED_FIXED: u32 = 0x00CA_0000; // 有标题栏/系统菜单/最小化，不可缩放
const WS_CHILD: u32 = 0x4000_0000;
const WS_VISIBLE: u32 = 0x1000_0000;
const WS_BORDER: u32 = 0x0080_0000;
const WS_TABSTOP: u32 = 0x0001_0000;
const ES_AUTOHSCROLL: u32 = 0x0080;
const SS_LEFT: u32 = 0x0000_0000;
const SS_LEFTNOWORDWRAP: u32 = 0x0000_000C;
const WM_CREATE: UINT = 0x0001;
const WM_DESTROY: UINT = 0x0002;
const WM_COMMAND: UINT = 0x0111;
const WM_SETFONT: UINT = 0x0030;
const WM_APP_STATUS: UINT = 0x8000 + 1;
const WM_APP_DONE: UINT = 0x8000 + 2;
const WM_APP_EDIT: UINT = 0x8000 + 3;
const WM_APP_AUTO: UINT = 0x8000 + 4;
const WM_APP_LABEL: UINT = 0x8000 + 5;
const SW_SHOW: i32 = 5;
const CW_USEDEFAULT: i32 = -2147483648i32;
const IDC_ARROW: LPCWSTR = 32512 as LPCWSTR;
const CF_UNICODETEXT: UINT = 13;
const GMEM_MOVEABLE: UINT = 0x0042;
const MB_ICONERROR: UINT = 0x10;

const ID_CONNECT: usize = 1001;
const ID_DISCONNECT: usize = 1002;
const ID_COPY: usize = 1003;

static H_STATUS: AtomicIsize = AtomicIsize::new(0);
static H_BTN_CONNECT: AtomicIsize = AtomicIsize::new(0);
static H_BTN_DISCONNECT: AtomicIsize = AtomicIsize::new(0);
static H_EDIT: AtomicIsize = AtomicIsize::new(0);
static H_MAIN: AtomicIsize = AtomicIsize::new(0);
static RUNNING: AtomicBool = AtomicBool::new(false);
static STOP: AtomicBool = AtomicBool::new(false);
static PENDING: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
/// 窗口还没建好就被要求自动连接时先记下来
static AUTO_WANTED: AtomicBool = AtomicBool::new(false);

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn get_text(h: HWND) -> String {
    let n = unsafe { GetWindowTextLengthW(h) };
    if n <= 0 {
        return String::new();
    }
    let mut buf = vec![0u16; (n + 1) as usize];
    let got = unsafe { GetWindowTextW(h, buf.as_mut_ptr(), n + 1) };
    String::from_utf16_lossy(&buf[..got.max(0) as usize])
}

fn set_status_direct(s: &str) {
    let h = H_STATUS.load(Ordering::Relaxed) as HWND;
    if !h.is_null() {
        unsafe { SetWindowTextW(h, wide(s).as_ptr()) };
    }
}

/// 供后台线程调用：把状态写进界面。
pub fn set_status(s: &str) {
    post_status(s.to_string())
}

/// 供后台线程调用：一条龙——填地址然后直接连上。
/// 好友双击就该能用，不该再点一次。
pub fn set_endpoint_and_connect(s: String) {
    set_endpoint(s);
    auto_connect();
}

/// 请求自动连接（地址拉取有结果之后调用；拉取失败也会调，用内置地址兜底）。
pub fn auto_connect() {
    let m = H_MAIN.load(Ordering::Relaxed) as HWND;
    if m.is_null() {
        AUTO_WANTED.store(true, Ordering::Relaxed);
        return;
    }
    unsafe { PostMessageW(m, WM_APP_AUTO, 0, 0) };
}

/// 供后台线程调用：把自动获取到的中继地址填进输入框。
pub fn set_endpoint(s: String) {
    let h = H_EDIT.load(Ordering::Relaxed) as HWND;
    if h.is_null() {
        PENDING.lock().unwrap_or_else(|e| e.into_inner()).replace(s);
        return;
    }
    let p = Box::into_raw(Box::new(s));
    let m = H_MAIN.load(Ordering::Relaxed) as HWND;
    if !m.is_null() {
        unsafe { PostMessageW(m, WM_APP_EDIT, 0, p as LPARAM) };
    }
}

fn post_status(s: String) {
    let h = H_MAIN.load(Ordering::Relaxed) as HWND;
    if h.is_null() {
        return;
    }
    let p = Box::into_raw(Box::new(s));
    unsafe { PostMessageW(h, WM_APP_STATUS, 0, p as LPARAM) };
}

/// 改「连接」按钮上的字。连上之后按钮是禁用的，只有把字也改掉才看得懂，
/// 否则用户看到一个灰着的「连接」会以为按不动。
fn post_connect_label(s: &str) {
    let m = H_MAIN.load(Ordering::Relaxed) as HWND;
    if m.is_null() {
        return;
    }
    let p = Box::into_raw(Box::new(s.to_string()));
    unsafe { PostMessageW(m, WM_APP_LABEL, 0, p as LPARAM) };
}

fn set_connect_label(s: &str) {
    let c = H_BTN_CONNECT.load(Ordering::Relaxed) as HWND;
    if !c.is_null() {
        unsafe { SetWindowTextW(c, wide(s).as_ptr()) };
    }
}

fn set_buttons(connected: bool) {
    let c = H_BTN_CONNECT.load(Ordering::Relaxed) as HWND;
    let d = H_BTN_DISCONNECT.load(Ordering::Relaxed) as HWND;
    if !c.is_null() {
        unsafe { EnableWindow(c, if connected { 0 } else { 1 }) };
    }
    if !d.is_null() {
        unsafe { EnableWindow(d, if connected { 1 } else { 0 }) };
    }
}

fn copy_to_clipboard(text: &str) {
    let w = wide(text);
    unsafe {
        if OpenClipboard(std::ptr::null_mut()) == 0 {
            return;
        }
        EmptyClipboard();
        let bytes = w.len() * 2;
        let mem = GlobalAlloc(GMEM_MOVEABLE, bytes);
        if !mem.is_null() {
            let dst = GlobalLock(mem) as *mut u16;
            if !dst.is_null() {
                std::ptr::copy_nonoverlapping(w.as_ptr(), dst, w.len());
                GlobalUnlock(mem);
                SetClipboardData(CF_UNICODETEXT, mem);
            }
        }
        CloseClipboard();
    }
}

/// 中继地址：支持 "host:port" 或完整 "https://host:port/路径/配置" 两种写法。
struct Endpoint {
    host: String,
    port: u16,
    token: String,
}

fn parse_endpoint(s: &str) -> Result<Endpoint, String> {
    let s = s.trim();
    if s.is_empty() {
        return Err("中继地址为空".into());
    }
    // 形如 host:port/token  或  wss://host:port/token
    let body = s
        .strip_prefix("wss://")
        .or_else(|| s.strip_prefix("ws://"))
        .or_else(|| s.strip_prefix("https://"))
        .or_else(|| s.strip_prefix("http://"))
        .unwrap_or(s);
    let (hostport, rest) = match body.find('/') {
        Some(i) => (&body[..i], body[i + 1..].trim_end_matches('/')),
        None => (body, ""),
    };
    let (host, port) = match hostport.rsplit_once(':') {
        Some((h, p)) => (h.to_string(), p.parse::<u16>().map_err(|_| "端口不合法".to_string())?),
        None => return Err("地址需要写成 主机:端口".into()),
    };
    if host.is_empty() {
        return Err("缺少主机名".into());
    }
    let token = rest.split('/').next().unwrap_or("").to_string();
    Ok(Endpoint { host, port, token })
}

fn start_connect(raw: String) {
    if RUNNING.load(Ordering::Relaxed) {
        return;
    }
    let ep = match parse_endpoint(&raw) {
        Ok(e) => e,
        Err(e) => {
            post_status(format!("地址无法识别：{e}"));
            return;
        }
    };
    RUNNING.store(true, Ordering::Relaxed);
    STOP.store(false, Ordering::Relaxed);
    set_buttons(true);
    post_connect_label("连接中…");
    post_status("正在连接中继…".into());

    thread::spawn(move || {
        let cb: Arc<dyn Fn(&str) + Send + Sync> = Arc::new(|s: &str| {
            post_status(s.to_string());
            // 「连接」按钮一直禁用着，所以必须靠文字表明当前到底在干什么
            if s.starts_with("已连接") {
                post_connect_label("已连接");
            } else if s.starts_with("连不上") || s.starts_with("重试中") {
                post_connect_label("连接中…");
            } else if s.starts_with("已断开") {
                post_connect_label("连接");
            }
        });
        let mut opts = ForwardOpts {
            host: ep.host,
            port: ep.port,
            token: ep.token,
            tls: true,
            listen: "127.0.0.1:25565".into(),
        };
        let mut cur_raw = raw.clone();

        // 连不上就一直重试：好友可以先开着窗口等，服主把服务器开起来后会自动接上，
        // 不用反复点「连接」。点「断开」时 STOP 置位，这里也就退出了。
        let mut first = true;
        let mut fails: u32 = 0;
        loop {
            if STOP.load(Ordering::Relaxed) {
                break;
            }
            match forward(opts.clone(), &STOP, cb.clone()) {
                Ok(()) => break,
                Err(e) => {
                    if STOP.load(Ordering::Relaxed) {
                        break;
                    }
                    println!("连接失败，将自动重试: {e}");
                    log(&format!("连接失败: {e}"));
                    fails += 1;
                    if first {
                        post_status("连不上服务器（服主可能还没开）
正在自动重试，开了会自动接上".into());
                        first = false;
                    } else {
                        post_status("还在重试…服务器开了会自动接上".into());
                    }
                    // 每 5 次失败（约 25 秒）去仓库看一眼地址是不是换了——
                    // CNB 环境被平台回收重建后地址会变，好友这边不该还要人手动改。
                    if fails % 5 == 0 {
                        if let Some(new_raw) = crate::refresh_endpoint() {
                            if new_raw != cur_raw {
                                match parse_endpoint(&new_raw) {
                                    Ok(p) => {
                                        log(&format!("中继地址已变：{cur_raw} → {new_raw}，自动切换"));
                                        opts = ForwardOpts {
                                            host: p.host,
                                            port: p.port,
                                            token: p.token,
                                            tls: true,
                                            listen: "127.0.0.1:25565".into(),
                                        };
                                        cur_raw = new_raw.clone();
                                        set_endpoint(new_raw.clone());
                                        post_status(format!(
                                            "中继地址变了，已自动切到
{new_raw}"
                                        ));
                                    }
                                    Err(e) => log(&format!("新地址看不懂，忽略: {new_raw} ({e})")),
                                }
                            }
                        }
                    }
                }
            }
            for _ in 0..50 {
                if STOP.load(Ordering::Relaxed) {
                    break;
                }
                thread::sleep(std::time::Duration::from_millis(100));
            }
        }

        RUNNING.store(false, Ordering::Relaxed);
        let h = H_MAIN.load(Ordering::Relaxed) as HWND;
        if !h.is_null() {
            unsafe { PostMessageW(h, WM_APP_DONE, 0, 0) };
        }
    });
}

unsafe extern "system" fn wndproc(h: HWND, m: UINT, w: WPARAM, l: LPARAM) -> LRESULT {
    match m {
        WM_CREATE => {
            let inst = GetModuleHandleW(std::ptr::null());
            let font = CreateFontW(
                -16,
                0,
                0,
                0,
                400,
                0,
                0,
                0,
                1,
                0,
                0,
                5,
                0,
                wide("Microsoft YaHei UI").as_ptr(),
            );
            let mk = |class: &str, text: &str, style: u32, x: i32, y: i32, cw: i32, ch: i32, id: usize| -> HWND {
                let hw = CreateWindowExW(
                    0,
                    wide(class).as_ptr(),
                    wide(text).as_ptr(),
                    style,
                    x,
                    y,
                    cw,
                    ch,
                    h,
                    id as HMENU,
                    inst,
                    std::ptr::null_mut(),
                );
                if !font.is_null() {
                    SendMessageW(hw, WM_SETFONT, font as WPARAM, 1);
                }
                hw
            };

            mk("STATIC", "中继地址", WS_CHILD | WS_VISIBLE | SS_LEFT, 16, 18, 68, 22, 0);
            let edit = mk(
                "EDIT",
                "",
                WS_CHILD | WS_VISIBLE | WS_BORDER | WS_TABSTOP | ES_AUTOHSCROLL,
                88,
                15,
                350,
                24,
                0,
            );
            H_EDIT.store(edit as isize, Ordering::Relaxed);

            mk("STATIC", "状态", WS_CHILD | WS_VISIBLE | SS_LEFT, 16, 56, 68, 22, 0);
            let st = mk("STATIC", "准备就绪", WS_CHILD | WS_VISIBLE | SS_LEFT, 88, 52, 350, 44, 0);
            H_STATUS.store(st as isize, Ordering::Relaxed);

            let bc = mk(
                "BUTTON",
                "连接",
                WS_CHILD | WS_VISIBLE | WS_TABSTOP | 0x0000_0001,
                88,
                108,
                96,
                30,
                ID_CONNECT,
            );
            H_BTN_CONNECT.store(bc as isize, Ordering::Relaxed);
            let bd = mk(
                "BUTTON",
                "断开",
                WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                192,
                108,
                96,
                30,
                ID_DISCONNECT,
            );
            H_BTN_DISCONNECT.store(bd as isize, Ordering::Relaxed);
            unsafe { EnableWindow(bd, 0) };
            // 自动连接马上就会把它置灰，文字得跟上
            unsafe { SetWindowTextW(bc, wide("连接中…").as_ptr()) };
            mk(
                "BUTTON",
                "复制游戏地址",
                WS_CHILD | WS_VISIBLE | WS_TABSTOP,
                302,
                108,
                100,
                30,
                ID_COPY,
            );

            mk(
                "STATIC",
                "连接成功后，在游戏里添加服务器：127.0.0.1:25565",
                WS_CHILD | WS_VISIBLE | SS_LEFTNOWORDWRAP,
                16,
                152,
                430,
                22,
                0,
            );

            // 窗口建好之前就拉到地址的话，这里补上
            let pend = PENDING.lock().unwrap_or_else(|e| e.into_inner()).take();
            if let Some(v) = pend {
                unsafe { SetWindowTextW(edit, wide(&v).as_ptr()) };
                unsafe { SetWindowTextW(st, wide("已自动填好地址，正在连接…").as_ptr()) };
            }
            if AUTO_WANTED.load(Ordering::Relaxed) {
                unsafe { PostMessageW(h, WM_APP_AUTO, 0, 0) };
            }
            0
        }

        WM_APP_STATUS => {
            let s = Box::from_raw(l as *mut String);
            set_status_direct(&s);
            0
        }
        WM_APP_LABEL => {
            let s = Box::from_raw(l as *mut String);
            set_connect_label(&s);
            0
        }
        WM_APP_AUTO => {
            let e = H_EDIT.load(Ordering::Relaxed) as HWND;
            if !e.is_null() {
                let raw = get_text(e);
                start_connect(raw);
            }
            0
        }
        WM_APP_EDIT => {
            let s = Box::from_raw(l as *mut String);
            let e = H_EDIT.load(Ordering::Relaxed) as HWND;
            if !e.is_null() {
                unsafe { SetWindowTextW(e, wide(&s).as_ptr()) };
            }
            set_status_direct("地址已更新，正在连接…");
            0
        }
        WM_APP_DONE => {
            set_buttons(false);
            0
        }

        WM_COMMAND => {
            let id = w & 0xFFFF;
            let code = (w >> 16) & 0xFFFF;
            if code == 0 {
                match id {
                    ID_CONNECT => {
                        let e = H_EDIT.load(Ordering::Relaxed) as HWND;
                        let raw = get_text(e);
                        start_connect(raw);
                    }
                    ID_DISCONNECT => {
                        STOP.store(true, Ordering::Relaxed);
                        set_status_direct("正在断开…");
                    }
                    ID_COPY => {
                        copy_to_clipboard("127.0.0.1:25565");
                        set_status_direct("已复制 127.0.0.1:25565，粘贴到游戏里即可");
                    }
                    _ => {}
                }
            }
            0
        }

        WM_DESTROY => {
            STOP.store(true, Ordering::Relaxed);
            PostQuitMessage(0);
            0
        }

        _ => DefWindowProcW(h, m, w, l),
    }
}

/// 启动界面并阻塞在消息循环里。`default_endpoint` 是自动填入框里的地址。
pub fn run(default_endpoint: String) -> i32 {
    unsafe {
        let inst = GetModuleHandleW(std::ptr::null());
        let class = wide("McBridgeWnd");
        let wc = WNDCLASSW {
            style: 0,
            lpfnWndProc: Some(wndproc),
            cbClsExtra: 0,
            cbWndExtra: 0,
            hInstance: inst,
            hIcon: std::ptr::null_mut(),
            hCursor: LoadCursorW(std::ptr::null_mut(), IDC_ARROW),
            hbrBackground: 16 as HBRUSH,
            lpszMenuName: std::ptr::null(),
            lpszClassName: class.as_ptr(),
        };
        RegisterClassW(&wc);

        let hwnd = CreateWindowExW(
            0,
            class.as_ptr(),
            wide("MC 中继客户端").as_ptr(),
            WS_OVERLAPPED_FIXED,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            470,
            216,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            inst,
            std::ptr::null_mut(),
        );
        if hwnd.is_null() {
            MessageBoxW(
                std::ptr::null_mut(),
                wide("创建窗口失败").as_ptr(),
                wide("错误").as_ptr(),
                MB_ICONERROR,
            );
            return 1;
        }
        H_MAIN.store(hwnd as isize, Ordering::Relaxed);

        let e = H_EDIT.load(Ordering::Relaxed) as HWND;
        if !e.is_null() && !default_endpoint.is_empty() {
            SetWindowTextW(e, wide(&default_endpoint).as_ptr());
        } else if !e.is_null() {
            set_status_direct("未能自动获取地址，请手动填写中继地址后点“连接”");
        }

        ShowWindow(hwnd, SW_SHOW);
        UpdateWindow(hwnd);

        let mut msg: MSG = std::mem::zeroed();
        while GetMessageW(&mut msg, std::ptr::null_mut(), 0, 0) > 0 {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        let _ = SetForegroundWindow(hwnd);
        0
    }
}
