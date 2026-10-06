// Initial phone profile defaults. No sandbox or TLS validation changes.
user_pref("browser.uidensity", 2);
user_pref("widget.wayland.ime.enabled", true);
user_pref("intl.locale.requested", "zh-CN");
user_pref("browser.shell.checkDefaultBrowser", false);
// Conservative rendering while the observed unexpected reboot is investigated.
user_pref("gfx.webrender.software", true);
user_pref("layers.acceleration.disabled", true);
