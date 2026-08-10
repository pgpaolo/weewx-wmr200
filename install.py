# installer for WMR200 weather station

from weecfg.extension import ExtensionInstaller


def loader():
    return WMR200Installer()


class WMR200Installer(ExtensionInstaller):
    """Installer for the Oregon Scientific WMR200"""

    def __init__(self):
        super(WMR200Installer, self).__init__(
            version="3.5.4-gp10-archive-clock-recovery",
            name='wmr200',
            description='WeeWX driver for the Oregon Scientific WMR200 station',
            author="Chris Manton; John E.P. Hynes; GP recovery hardening",
            config={
                'WMR200': {
                    'model': 'WMR200',
                    'use_pc_time': 'True',
                    'erase_archive': 'False',
                    'archive_interval': '60',
                    'ignore_checksum': 'False',
                    'archive_startup': '120',
                    'archive_clock_drift_max': '900',
                    'archive_clock_wait': '180',
                    'archive_recovery_resume': 'True',
                    'archive_recovery_state_path': '/var/lib/weewx/wmr200-archive-recovery.json',
                    'archive_logger_interval': '0',
                    'archive_threshold': '1512000',
                    'sensor_status': 'True',
                    'usb_write_retries': '3',
                    'usb_read_retries': '2',
                    'usb_retry_delay': '0.5',
                    'usb_reopen_on_failure': 'True',
                    'usb_read_slice_timeout': '2.0',
                    'usb_logical_timeout_seconds': '15',
                    'developer_trace': 'True',
                    'developer_trace_path': '/var/log/weewx/wmr200-developer-trace.jsonl',
                    'developer_trace_max_mb': '10',
                    'developer_trace_backups': '4',
                    'developer_trace_queue_size': '4096',
                    'developer_trace_include_timeouts': 'True',
                    'developer_trace_include_packets': 'True',
                    'driver_file_log': 'True',
                    'driver_file_log_path': '/var/log/weewx/wmr200-debug.log',
                    'driver_file_log_level': 'DEBUG',
                    'driver_file_log_max_mb': '10',
                    'driver_file_log_backups': '4',
                    'sensor_map': {
                    }
                }
            },
            files=[
                ('bin/user', ['bin/user/wmr200.py']),
            ]
        )
