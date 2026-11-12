/* rmnet-link-test - exercise libqmi's netlink link creation exactly the way
 * ModemManager's multiplex (QMAP) bearer does, but WITHOUT opening the QMI
 * device: the link is created over netlink on top of an existing netdev, the
 * QMI device object is only needed as an API handle.
 *
 * usage:
 *   rmnet-link-test add <base_ifname> <mux> <prefix> [flags]
 *   rmnet-link-test del <ifname> <mux>
 *
 * flags: QmiDeviceAddLinkFlags bitmask (0 = none, 2 = INGRESS_MAP_CKSUMV4,
 *        6 = ingress+egress cksumv4, ...).
 *
 * Exit: 0 ok, 2 add failed, 3 delete failed, 4 usage/ctor failed.
 */
#include <gio/gio.h>
#include <libqmi-glib.h>
#include <stdio.h>
#include <string.h>

static GMainLoop *loop = NULL;
static int rc = 0;
static QmiDevice *dev = NULL;
static const char *mode = NULL;
static const char *arg_ifname = NULL;
static guint arg_mux = 0;
static const char *arg_prefix = NULL;
static guint arg_flags = 0;

static void add_done (GObject *src, GAsyncResult *res, gpointer user_data)
{
    GError *error = NULL;
    guint mux_id = 0;
    gchar *name = qmi_device_add_link_with_flags_finish (QMI_DEVICE (src), res, &mux_id, &error);
    if (!name) {
        g_printerr ("add_link failed: %s\n", error ? error->message : "unknown");
        rc = 2;
    } else {
        g_print ("add_link ok: ifname=%s mux_id=%u\n", name, mux_id);
        g_free (name);
    }
    g_main_loop_quit (loop);
}

static void del_done (GObject *src, GAsyncResult *res, gpointer user_data)
{
    GError *error = NULL;
    if (!qmi_device_delete_link_finish (QMI_DEVICE (src), res, &error)) {
        g_printerr ("delete_link failed: %s\n", error ? error->message : "unknown");
        rc = 3;
    } else {
        g_print ("delete_link ok\n");
    }
    g_main_loop_quit (loop);
}

static void device_ready (GObject *src, GAsyncResult *res, gpointer user_data)
{
    GError *error = NULL;
    dev = qmi_device_new_finish (res, &error);
    if (!dev) {
        g_printerr ("qmi_device_new failed: %s\n", error ? error->message : "unknown");
        rc = 4;
        g_main_loop_quit (loop);
        return;
    }
    g_print ("device handle: %s\n", qmi_device_get_path_display (dev));

    if (!strcmp (mode, "add"))
        qmi_device_add_link_with_flags (dev, arg_mux, arg_ifname, arg_prefix, arg_flags, NULL, add_done, NULL);
    else
        qmi_device_delete_link (dev, arg_ifname, arg_mux, NULL, del_done, NULL);
}

int main (int argc, char **argv)
{
    if (argc >= 5 && !strcmp (argv[1], "add")) {
        mode = "add";
        arg_ifname = argv[2];
        arg_mux = (guint) g_ascii_strtoull (argv[3], NULL, 0);
        arg_prefix = argv[4];
        if (argc >= 6)
            arg_flags = (guint) g_ascii_strtoull (argv[5], NULL, 0);
    } else if (argc == 4 && !strcmp (argv[1], "del")) {
        mode = "del";
        arg_ifname = argv[2];
        arg_mux = (guint) g_ascii_strtoull (argv[3], NULL, 0);
    } else {
        g_printerr ("usage: %s add <base_ifname> <mux> <prefix> [flags]\n"
                    "       %s del <ifname> <mux>\n", argv[0], argv[0]);
        return 4;
    }

    loop = g_main_loop_new (NULL, FALSE);
    /* The QMI node is only an API handle here; it is never opened, so no QMI
     * traffic reaches the modem.  Any existing character device will do. */
    qmi_device_new (g_file_new_for_path ("/dev/wwan0qmi0"), NULL, device_ready, NULL);
    g_main_loop_run (loop);
    return rc;
}
