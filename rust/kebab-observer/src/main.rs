#![forbid(unsafe_code)]
// The JSON session/closeout records are wide: serde_json's json! macro spends
// macro recursion per key, and the default limit is below what the closeout
// record needs. The value is a compile-time budget, not a runtime one.
#![recursion_limit = "512"]

#[cfg(target_os = "linux")]
mod live;
#[cfg(target_os = "linux")]
mod tls_live;
#[cfg(target_os = "linux")]
mod tls_attach;
#[cfg(target_os = "linux")]
mod track;

fn main() {
    #[cfg(target_os = "linux")]
    {
        let code = live::dispatch();
        if code != 0 {
            eprintln!("observer rejected with code {code}");
        }
        std::process::exit(i32::from(code));
    }
    #[cfg(not(target_os = "linux"))]
    {
        eprintln!("live observer requires Linux; offline tests are portable");
        std::process::exit(2);
    }
}
