# installer for the Oregon Scientific WMR200 weather station

from weecfg.extension import ExtensionInstaller


def loader():
    return WMR200Installer()


class WMR200Installer(ExtensionInstaller):
    """Installer for the Oregon Scientific WMR200 hardened driver."""

    def __init__(self):
        super(WMR200Installer, self).__init__(
            version="3.5.4-gp7-streamresync",
            name="wmr200",
            description=(
                "Hardened WeeWX driver for the Oregon Scientific "
                "WMR200/WMR200A station"
            ),
            author="Chris Manton and contributors; GP hardened fork",
            config={
                "WMR200": {
                    "model": "WMR200",
                    "driver": "user.wmr200",
                    "use_pc_time": "True",
                    "erase_archive": "False",
                    "archive_interval": "60",
                    "archive_startup": "120",
                    "archive_threshold": "604800",
                    "ignore_checksum": "False",
                    "sensor_status": "True",
                    "usb_write_retries": "3",
                    "usb_read_retries": "2",
                    "usb_retry_delay": "0.5",
                    "usb_reopen_on_failure": "True",
                    "usb_timeout_warn_consecutive": "2",
                    "usb_timeout_error_consecutive": "4",
                    "usb_health_interval": "300",
                    "developer_trace": "True",
                    "developer_trace_path": (
                        "/var/log/weewx/wmr200-developer-trace.jsonl"
                    ),
                    "developer_trace_max_mb": "20",
                    "developer_trace_backups": "5",
                    "developer_trace_queue_size": "4096",
                    "developer_trace_include_timeouts": "True",
                    "developer_trace_include_packets": "True",
                    "sensor_map": {},
                }
            },
            files=[
                ("bin/user", ["bin/user/wmr200.py"]),
            ],
        )
