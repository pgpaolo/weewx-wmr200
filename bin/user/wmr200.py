# Copyright (c) 2013 Chris Manton <cmanton@gmail.com>  www.onesockoff.org
# See the file LICENSE.txt for your full rights.
#
# Special recognition to Lars de Bruin <l...@larsdebruin.net> for contributing
# packet decoding code.
#
# pylint parameters
# suppress global variable warnings
#   pylint: disable-msg=W0603
# suppress weewx driver methods not implemented
#   pylint: disable-msg=W0223  
# suppress weewx driver methods non-conforming name
#   pylint: disable-msg=C0103
# suppress too many lines in module
#   pylint: disable-msg=C0302
# suppress too many instance attributes
#   pylint: disable-msg=R0902
# suppress too many public methods
#   pylint: disable-msg=R0904
# suppress too many statements
#   pylint: disable-msg=R0915
# suppress unused arguments   e.g. loader(...,engine)
#   pylint: disable-msg=W0613
"""Classes and functions to interface with an Oregon Scientific WMR200 station

WMR200:
 - logger
 - up to 10 channels

Oregon Scientific
  http://us.oregonscientific.com/ulimages/manuals2/WMR200.pdf

Bronberg Weather Station
  For a pretty good summary of what's in these packets see
  http://www.bashewa.com/wmr200-protocol.php

The WMR200 does not report wind gust direction. 
"""

from __future__ import absolute_import
from __future__ import print_function
import datetime
import errno
import json
import logging
import os
import queue
import select
import socket
import threading
import time
import usb

import weewx.drivers
import weeutil.weeutil

DRIVER_NAME = 'WMR200'
DRIVER_VERSION = "3.5.4-gp9-live-scheduler"

log = logging.getLogger(__name__)


class _DriverRootForwardHandler(logging.Handler):
    """Forward records to the root logger without enabling DEBUG in syslog."""

    def emit(self, record):
        try:
            logging.getLogger().handle(record)
        except Exception:
            pass


class _AsyncDriverFileHandler(logging.Handler):
    """Format driver records and enqueue them without blocking acquisition."""

    def __init__(self, owner, level=logging.DEBUG):
        super(_AsyncDriverFileHandler, self).__init__(level)
        self.owner = owner

    def emit(self, record):
        try:
            self.owner.enqueue(self.format(record))
        except Exception:
            self.owner.writer_errors += 1


class DriverFileLog(object):
    """Best-effort asynchronous rotating text log for this driver only.

    File I/O runs in a dedicated daemon thread. Queue saturation or write
    failures never propagate into the weather acquisition path.
    """

    def __init__(self, enabled=False, path='/var/log/weewx/wmr200-debug.log',
                 level='DEBUG', max_mb=10, backups=4, queue_size=4096):
        self.enabled = bool(enabled)
        self.requested_path = os.path.abspath(os.path.expanduser(str(path)))
        self.path = self.requested_path
        self.max_bytes = max(1024 * 1024, int(max_mb) * 1024 * 1024)
        self.backups = max(1, int(backups))
        self.queue_size = max(128, int(queue_size))
        self.records_written = 0
        self.records_dropped = 0
        self.writer_errors = 0
        self._queue = None
        self._thread = None
        self._stop_event = threading.Event()
        self._handler = None
        self._root_forwarder = None
        self._previous_level = log.level
        self._previous_effective_level = log.getEffectiveLevel()
        self._previous_propagate = log.propagate

        level_name = str(level).upper().strip()
        self.level_name = level_name if level_name in (
            'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL') else 'DEBUG'
        self.level = getattr(logging, self.level_name)

        if not self.enabled:
            return

        try:
            candidates = [self.requested_path]
            fallback_path = '/tmp/wmr200-debug.log'
            if fallback_path not in candidates:
                candidates.append(fallback_path)

            errors = []
            selected_path = None
            for candidate in candidates:
                try:
                    directory = os.path.dirname(candidate)
                    if directory and not os.path.isdir(directory):
                        os.makedirs(directory, exist_ok=True)
                    with open(candidate, 'a', encoding='utf-8'):
                        pass
                    selected_path = candidate
                    break
                except Exception as exception:
                    errors.append('%s: %s' % (candidate, exception))

            if selected_path is None:
                raise OSError('No writable driver log destination; %s' %
                              '; '.join(errors))

            self.path = selected_path
            self._queue = queue.Queue(maxsize=self.queue_size)
            self._thread = threading.Thread(
                target=self._writer_loop, name='WMR200DriverFileLog')
            self._thread.daemon = True

            self._handler = _AsyncDriverFileHandler(self, self.level)
            self._handler.setFormatter(logging.Formatter(
                '%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s'))

            # To capture DEBUG in the private file without flooding the normal
            # WeeWX/root handlers, stop propagation and forward only records
            # that would have passed the logger's previous effective level.
            if self._previous_propagate:
                self._root_forwarder = _DriverRootForwardHandler(
                    level=self._previous_effective_level)
                log.addHandler(self._root_forwarder)
                log.propagate = False

            log.addHandler(self._handler)
            log.setLevel(min(self._previous_effective_level, self.level))
            self._thread.start()

            if self.path != self.requested_path:
                logging.getLogger().warning(
                    'WMR200 driver file log path %s is not writable; using %s',
                    self.requested_path, self.path)
        except Exception as exception:
            self.enabled = False
            self.writer_errors += 1
            self._restore_logger()
            logging.getLogger().error(
                'Unable to enable WMR200 driver file log at %s: %s. '
                'Driver will continue without it.', self.path, exception)

    def enqueue(self, line):
        if not self.enabled or self._queue is None:
            return
        try:
            self._queue.put_nowait(str(line) + '\n')
        except queue.Full:
            self.records_dropped += 1
        except Exception:
            self.writer_errors += 1

    def _rotate_files(self):
        oldest = '%s.%d' % (self.path, self.backups)
        if os.path.exists(oldest):
            os.remove(oldest)
        for index in range(self.backups - 1, 0, -1):
            source = '%s.%d' % (self.path, index)
            target = '%s.%d' % (self.path, index + 1)
            if os.path.exists(source):
                os.replace(source, target)
        if os.path.exists(self.path):
            os.replace(self.path, self.path + '.1')

    def _writer_loop(self):
        handle = None
        try:
            while not self._stop_event.is_set() or not self._queue.empty():
                try:
                    line = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    encoded_length = len(line.encode('utf-8'))
                    if handle is None:
                        handle = open(self.path, 'a', buffering=1,
                                      encoding='utf-8')
                    try:
                        current_size = os.path.getsize(self.path)
                    except OSError:
                        current_size = 0
                    if current_size + encoded_length > self.max_bytes:
                        handle.flush()
                        handle.close()
                        handle = None
                        self._rotate_files()
                        handle = open(self.path, 'a', buffering=1,
                                      encoding='utf-8')
                    handle.write(line)
                    self.records_written += 1
                finally:
                    self._queue.task_done()
        except Exception as exception:
            self.writer_errors += 1
            self.enabled = False
            logging.getLogger().error(
                'WMR200 driver file logger stopped after error: %s. '
                'Weather acquisition will continue.', exception)
        finally:
            if handle is not None:
                try:
                    handle.flush()
                    handle.close()
                except Exception:
                    pass

    def _restore_logger(self):
        if self._handler is not None:
            try:
                log.removeHandler(self._handler)
            except Exception:
                pass
            self._handler = None
        if self._root_forwarder is not None:
            try:
                log.removeHandler(self._root_forwarder)
            except Exception:
                pass
            self._root_forwarder = None
        log.setLevel(self._previous_level)
        log.propagate = self._previous_propagate

    def stop(self, timeout=5.0):
        if self._thread is None:
            self._restore_logger()
            return
        self._restore_logger()
        self._stop_event.set()
        self._thread.join(timeout)
        if self._thread.is_alive():
            logging.getLogger().warning(
                'WMR200 driver file logger did not stop within %.1fs', timeout)


class DeveloperTrace(object):
    """Non-blocking, rotating JSONL trace for USB and protocol diagnostics.

    Trace production is deliberately best-effort: queue saturation, directory
    permission errors, or disk failures never propagate into the weather
    driver. This keeps developer logging from affecting station acquisition.
    """

    def __init__(self, enabled=True, path='/var/log/weewx/wmr200-developer-trace.jsonl',
                 max_mb=10, backups=4, queue_size=4096,
                 include_timeouts=True, include_packets=True):
        self.enabled = bool(enabled)
        self.requested_path = os.path.abspath(os.path.expanduser(str(path)))
        self.path = self.requested_path
        self.max_bytes = max(1024 * 1024, int(max_mb) * 1024 * 1024)
        self.backups = max(1, int(backups))
        self.queue_size = max(128, int(queue_size))
        self.include_timeouts = bool(include_timeouts)
        self.include_packets = bool(include_packets)
        self.records_written = 0
        self.records_dropped = 0
        self.writer_errors = 0
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._queue = None
        self._thread = None
        self._start_monotonic = time.monotonic()

        if not self.enabled:
            return

        try:
            # Try the configured location first. If the WeeWX service account
            # cannot create/write it, fall back to a normally writable path.
            # The actual destination is always reported in syslog.
            candidates = [self.requested_path]
            fallback_path = '/tmp/wmr200-developer-trace.jsonl'
            if fallback_path not in candidates:
                candidates.append(fallback_path)

            destination_errors = []
            selected_path = None
            for candidate in candidates:
                try:
                    directory = os.path.dirname(candidate)
                    if directory and not os.path.isdir(directory):
                        os.makedirs(directory, exist_ok=True)
                    # Verify append access without truncating an existing log.
                    with open(candidate, 'a', encoding='utf-8'):
                        pass
                    selected_path = candidate
                    break
                except Exception as destination_exception:
                    destination_errors.append('%s: %s' %
                                              (candidate, destination_exception))

            if selected_path is None:
                raise OSError('No writable trace destination; %s' %
                              '; '.join(destination_errors))

            self.path = selected_path
            if self.path != self.requested_path:
                log.warning('WMR200 developer trace path %s is not writable; '
                            'using fallback %s' %
                            (self.requested_path, self.path))

            self._queue = queue.Queue(maxsize=self.queue_size)
            self._thread = threading.Thread(
                target=self._writer_loop,
                name='WMR200DeveloperTrace')
            self._thread.daemon = True
            self._thread.start()
            self.event('EVENT', 'trace_started',
                       trace_path=self.path,
                       max_bytes=self.max_bytes,
                       backups=self.backups,
                       queue_size=self.queue_size,
                       include_timeouts=self.include_timeouts,
                       include_packets=self.include_packets)
            log.warning('WMR200 developer trace ENABLED: %s' % self.path)
        except Exception as exception:
            self.enabled = False
            self.writer_errors += 1
            log.error('Unable to enable WMR200 developer trace at %s: %s. '
                      'Driver will continue without developer trace.' %
                      (self.path, exception))

    @staticmethod
    def _hex(data):
        if data is None:
            return None
        try:
            return ' '.join('%02x' % (int(value) & 0xff) for value in data)
        except (TypeError, ValueError):
            return str(data)

    @staticmethod
    def _json_safe(value):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, bytes):
            return value.hex()
        if isinstance(value, (list, tuple)):
            return [DeveloperTrace._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): DeveloperTrace._json_safe(item)
                    for key, item in value.items()}
        return str(value)

    def event(self, direction, event_name, data=None, **fields):
        """Queue one trace record without waiting for disk I/O."""
        if not self.enabled or self._queue is None:
            return
        if (event_name in ('usb_read_timeout', 'usb_poll_timeout') and
                not self.include_timeouts):
            return

        try:
            with self._sequence_lock:
                self._sequence += 1
                sequence = self._sequence
            now = datetime.datetime.now(datetime.timezone.utc)
            record = {
                'timestamp_utc': now.isoformat(timespec='milliseconds'),
                'elapsed_s': round(time.monotonic() - self._start_monotonic, 6),
                'sequence': sequence,
                'thread': threading.current_thread().name,
                'direction': str(direction),
                'event': str(event_name),
            }
            if data is not None:
                try:
                    record['length'] = len(data)
                except TypeError:
                    pass
                record['hex'] = self._hex(data)
            for key, value in fields.items():
                record[str(key)] = self._json_safe(value)
            self._queue.put_nowait(record)
        except queue.Full:
            self.records_dropped += 1
            if self.records_dropped == 1 or self.records_dropped % 100 == 0:
                log.warning('WMR200 developer trace queue full; dropped=%d' %
                            self.records_dropped)
        except Exception as exception:
            # Diagnostic logging must never interfere with acquisition.
            self.writer_errors += 1
            if self.writer_errors == 1:
                log.error('WMR200 developer trace enqueue error: %s' % exception)

    def _rotate_files(self):
        oldest = '%s.%d' % (self.path, self.backups)
        if os.path.exists(oldest):
            os.remove(oldest)
        for index in range(self.backups - 1, 0, -1):
            source = '%s.%d' % (self.path, index)
            target = '%s.%d' % (self.path, index + 1)
            if os.path.exists(source):
                os.replace(source, target)
        if os.path.exists(self.path):
            os.replace(self.path, self.path + '.1')

    def _writer_loop(self):
        handle = None
        try:
            while not self._stop_event.is_set() or not self._queue.empty():
                try:
                    record = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                line = json.dumps(record, sort_keys=True,
                                  separators=(',', ':'), ensure_ascii=False) + '\n'
                encoded_length = len(line.encode('utf-8'))
                try:
                    if handle is None:
                        handle = open(self.path, 'a', buffering=1,
                                      encoding='utf-8')
                    try:
                        current_size = os.path.getsize(self.path)
                    except OSError:
                        current_size = 0
                    if current_size + encoded_length > self.max_bytes:
                        handle.flush()
                        handle.close()
                        handle = None
                        self._rotate_files()
                        handle = open(self.path, 'a', buffering=1,
                                      encoding='utf-8')
                    handle.write(line)
                    self.records_written += 1
                finally:
                    self._queue.task_done()
        except Exception as exception:
            self.writer_errors += 1
            self.enabled = False
            log.error('WMR200 developer trace writer stopped after error: %s. '
                      'Weather acquisition will continue.' % exception)
        finally:
            if handle is not None:
                try:
                    handle.flush()
                    handle.close()
                except Exception:
                    pass

    def stop(self, timeout=5.0):
        """Drain queued records and stop the writer without blocking forever."""
        if self._thread is None:
            return
        self.event('EVENT', 'trace_stopping',
                   records_written=self.records_written,
                   records_dropped=self.records_dropped,
                   writer_errors=self.writer_errors)
        self._stop_event.set()
        self._thread.join(timeout)
        if self._thread.is_alive():
            log.warning('WMR200 developer trace writer did not stop within %.1fs; '
                        'continuing shutdown' % timeout)
        else:
            log.info('WMR200 developer trace stopped: written=%d dropped=%d errors=%d' %
                     (self.records_written, self.records_dropped,
                      self.writer_errors))


def loader(config_dict, engine):  # @UnusedVariable
    return WMR200(**config_dict[DRIVER_NAME])

def confeditor_loader():
    return WMR200ConfEditor()


# General decoding sensor maps.
WIND_DIR_MAP = {0: 'N', 1: 'NNE', 2: 'NE', 3: 'ENE',
                4: 'E', 5: 'ESE', 6: 'SE', 7: 'SSE',
                8: 'S', 9: 'SSW', 10: 'SW', 11: 'WSW',
                12: 'W', 13: 'WNW', 14: 'NW', 15: 'NNW'}
FORECAST_MAP = {0: 'Partly Cloudy', 1: 'Rainy', 2: 'Cloudy',
                3: 'Sunny', 4: 'Clear Night', 5: 'Snowy',
                6: 'Partly Cloudy Night', 7: 'Unknown7'}
TRENDS = {0: 'Stable', 1: 'Rising', 2: 'Falling', 3: 'Undefined'}

# Size of USB frame to read from weather console.
_WMR200_USB_FRAME_SIZE = 8

# Time to sleep in seconds between querying usb device thread
# for data.  This should be non-zero and reduces load on the machine.
_WMR200_USB_POLL_INTERVAL = 1

# Time interval in secs to send data to the wmr200 to request live data.
_WMR200_REQUEST_LIVE_DATA_INTERVAL = 30

# Keep each blocking interrupt read short. The same lock serializes PyUSB
# reads and control writes, so a long blocking read can delay the D0 heartbeat.
# gp9 uses short read slices while preserving the historical 15-second
# communication-timeout semantics separately.
_WMR200_USB_READ_DATA_INTERVAL = 2.0
_WMR200_USB_LOGICAL_TIMEOUT_INTERVAL = 15.0

# Time in ms to wait for USB control transfers to complete.
_WMR200_USB_RESET_TIMEOUT = 1000

# USB recovery defaults. These can be overridden in the [WMR200] stanza.
_WMR200_USB_WRITE_RETRIES = 3
_WMR200_USB_READ_RETRIES = 2
_WMR200_USB_RETRY_DELAY = 0.5
_WMR200_USB_REOPEN_DELAY = 1.0

# Internal queue marker. It is inserted by the USB polling thread exactly
# where a malformed HID report caused bytes to be lost. The protocol parser
# can then abandon only the incomplete packet and resume at the next command.
_WMR200_USB_STREAM_GAP_MARKER = 'wmr200_usb_stream_gap'

# Guessed wmr200 protocol max packet size in bytes.
# This is only a screen to differentiate between good and
# bad packets.
_WMR200_MAX_PACKET_SIZE = 0x80

# Driver name.
_WMR200_DRIVER_NAME = 'wmr200'

# weewx configurable flags for enabling/disabling debug verbosity.
# Prints processed packets with context from console.
DEBUG_PACKETS_COOKED = 0
# Prints raw pre-processed packets from console.
DEBUG_PACKETS_RAW = 0
# Prints respective packets individually.
DEBUG_PACKETS_ARCHIVE = 0
DEBUG_PACKETS_PRESSURE = 0
DEBUG_PACKETS_RAIN = 0
DEBUG_PACKETS_STATUS = 0
DEBUG_PACKETS_TEMP = 0
DEBUG_PACKETS_UVI = 0
DEBUG_PACKETS_WIND = 0
# Print communication messages 
DEBUG_COMM = 0
# Print weather station configuration.
DEBUG_CONFIG_DATA = 0
# Print all writes to weather console.
DEBUG_WRITES = 0
DEBUG_READS = 0
DEBUG_CHECKSUM = 0
# Print mapping from sensors to database fields
DEBUG_MAPPING = 0


class WMR200PacketParsingError(Exception):
    """A driver handled recoverable packet parsing error condition."""
    def __init__(self, msg):
        super(WMR200PacketParsingError, self).__init__()
        self._msg = msg

    @property
    def msg(self):
        """Exception message to be logged to console."""
        return self._msg


class WMR200ProtocolError(weewx.WeeWxIOError):
    """Used to signal a protocol error condition."""
    def __init__(self, msg):
        # Preserve the message in the base exception so str(exception) is useful.
        super(WMR200ProtocolError, self).__init__(msg)
        self._msg = msg
        log.error(msg)

    @property
    def msg(self):
        """Exception message to be logged to console."""
        return self._msg


class UsbDevice(object):
    """Serialize USB access and recover from transient libusb failures."""

    def __init__(self, trace=None):
        self.trace = trace
        # One blocking interrupt-read slice. Keep this short so control
        # writes (especially the D0 live heartbeat) cannot sit behind the USB
        # I/O lock for the historical 15-second read timeout.
        self.timeout_read = _WMR200_USB_READ_DATA_INTERVAL
        # Logical communication timeout. Multiple short read slices are
        # combined into one historical-style timeout every 15 seconds of
        # continuous silence. This keeps monitoring/recovery semantics stable.
        self.logical_timeout_interval = _WMR200_USB_LOGICAL_TIMEOUT_INTERVAL
        # USB device used for libusb.
        self.dev = None
        # Holds device handle for access.
        self.handle = None
        # Device identity, retained so the handle can be reopened.
        self.vendor_id = None
        self.product_id = None
        # Debug byte counts. Bytes are counted only after successful I/O.
        self.byte_cnt_rd = 0
        self.byte_cnt_wr = 0
        # Default to a sane endpoint.
        self.in_endpoint = usb.ENDPOINT_IN + 1
        # Only one interface.
        self.interface = 0
        # Recovery policy.
        self.write_retries = _WMR200_USB_WRITE_RETRIES
        self.read_retries = _WMR200_USB_READ_RETRIES
        self.retry_delay = _WMR200_USB_RETRY_DELAY
        self.reopen_delay = _WMR200_USB_REOPEN_DELAY
        self.reopen_on_failure = True
        self.reopen_count = 0
        # read_poll_timeout_count counts short scheduling slices that returned
        # no data. read_timeout_count counts logical 15-second communication
        # timeouts, preserving the gp7/gp8 health semantics.
        self.read_poll_timeout_count = 0
        self.read_timeout_count = 0
        self._logical_timeout_level = 0
        self.read_pipe_stall_count = 0
        self.write_transient_error_count = 0
        self.malformed_report_count = 0

        # Result of the most recent blocking read. PollUsbDevice uses this to
        # distinguish ordinary no-data timeouts from a real byte-stream gap.
        self.last_read_status = 'initial'
        self.stream_gap_count = 0
        self.last_stream_gap_reason = None

        # USB health monitoring. A single interrupt-read timeout usually means
        # only that no HID report arrived inside the polling window; it is not
        # proof of a lost weather packet. Consecutive timeouts and recovery are
        # tracked separately so the dashboard can distinguish normal silence
        # from a real communication degradation.
        self.successful_read_count = 0
        self.consecutive_read_timeouts = 0
        self.max_consecutive_read_timeouts = 0
        self.timeout_burst_count = 0
        self.timeout_warn_consecutive = 2
        self.timeout_error_consecutive = 4
        self.health_interval = 300.0
        self._monitor_start_monotonic = time.monotonic()
        self._last_success_monotonic = None
        self._last_success_utc = None
        self._last_health_monotonic = self._monitor_start_monotonic

        # PyUSB 0.x handles are not guaranteed to be safe for simultaneous
        # interruptRead() and controlMsg() calls from different threads.
        self._io_lock = threading.RLock()

    def _trace(self, direction, event_name, data=None, **fields):
        if self.trace is not None:
            self.trace.event(direction, event_name, data=data, **fields)

    @staticmethod
    def _utc_timestamp():
        return datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec='milliseconds')

    def _seconds_since_last_success(self, now_monotonic=None):
        if now_monotonic is None:
            now_monotonic = time.monotonic()
        reference = self._last_success_monotonic
        if reference is None:
            reference = self._monitor_start_monotonic
        return max(0.0, now_monotonic - reference)

    def _timeout_health(self):
        consecutive = self.consecutive_read_timeouts
        if consecutive >= self.timeout_error_consecutive:
            return 'ERROR', 'degraded'
        if consecutive >= self.timeout_warn_consecutive:
            return 'WARNING', 'warning'
        return 'INFO', 'healthy'

    def _trace_health_snapshot(self, trigger='periodic', force=False,
                               now_monotonic=None):
        if now_monotonic is None:
            now_monotonic = time.monotonic()
        if not force and (now_monotonic - self._last_health_monotonic) < self.health_interval:
            return
        severity, health_state = self._timeout_health()
        self._trace(
            'HEALTH', 'usb_health_snapshot',
            severity=severity,
            health_state=health_state,
            trigger=trigger,
            successful_reads=self.successful_read_count,
            read_timeouts=self.read_timeout_count,
            poll_slice_timeouts=self.read_poll_timeout_count,
            read_slice_seconds=self.timeout_read,
            logical_timeout_seconds=self.logical_timeout_interval,
            timeout_bursts=self.timeout_burst_count,
            timeout_consecutive=self.consecutive_read_timeouts,
            max_consecutive_timeouts=self.max_consecutive_read_timeouts,
            seconds_since_last_success=round(
                self._seconds_since_last_success(now_monotonic), 3),
            last_success_utc=self._last_success_utc,
            read_pipe_stalls=self.read_pipe_stall_count,
            write_transient_errors=self.write_transient_error_count,
            malformed_reports=self.malformed_report_count,
            reopens=self.reopen_count)
        self._last_health_monotonic = now_monotonic

    def _mark_successful_read(self):
        now_monotonic = time.monotonic()
        recovered_timeouts = self.consecutive_read_timeouts
        silence_seconds = self._seconds_since_last_success(now_monotonic)

        self.successful_read_count += 1
        self._last_success_monotonic = now_monotonic
        self._last_success_utc = self._utc_timestamp()
        self.consecutive_read_timeouts = 0
        self._logical_timeout_level = 0

        if recovered_timeouts:
            recovery_severity = ('WARNING'
                                 if recovered_timeouts >= self.timeout_error_consecutive
                                 else 'INFO')
            self._trace(
                'EVENT', 'usb_read_recovered',
                severity=recovery_severity,
                health_state='healthy',
                classification='automatic_recovery',
                recovered_timeouts=recovered_timeouts,
                silence_seconds=round(silence_seconds, 3),
                timeout_total=self.read_timeout_count,
                successful_reads=self.successful_read_count,
                impact='communication_resumed',
                action='none')

        self._trace_health_snapshot(
            trigger='successful_read', now_monotonic=now_monotonic)

    @staticmethod
    def _find_dev(vendor_id, product_id, device_id=None):
        """Find the vendor and product ID on the USB bus."""
        for bus in usb.busses():
            for dev in bus.devices:
                if dev.idVendor == vendor_id and dev.idProduct == product_id:
                    if device_id is None or dev.filename == device_id:
                        log.debug('Found station at bus=%s device=%s' %
                                  (bus.dirname, dev.filename))
                        return dev
        return None

    @staticmethod
    def _error_number(exception):
        """Return a normalized positive errno, if the backend supplied one."""
        value = getattr(exception, 'errno', None)
        if value is None:
            for arg in getattr(exception, 'args', ()):
                if isinstance(arg, int):
                    value = arg
                    break
        try:
            return abs(int(value))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _is_timeout(cls, exception):
        """Recognize timeout/no-data errors across libusb/PyUSB backends."""
        number = cls._error_number(exception)
        message = ('%s %r' % (exception, exception)).lower()
        return (number == errno.ETIMEDOUT or
                'no data available' in message or
                'timed out' in message or
                'timeout' in message)

    @classmethod
    def _is_pipe_stall(cls, exception):
        """Recognize EPIPE even when a backend exposes it only as text."""
        number = cls._error_number(exception)
        message = ('%s %r' % (exception, exception)).lower()
        return (number == errno.EPIPE or
                'pipe error' in message or
                'broken pipe' in message or
                'pipe stall' in message)

    @staticmethod
    def _release_interface(handle, interface):
        """Release an interface with both known PyUSB 0.x call signatures."""
        try:
            handle.releaseInterface(interface)
        except TypeError:
            handle.releaseInterface()

    def _open_device_unlocked(self, vendor_id, product_id):
        """Open and claim the device. Caller must hold _io_lock."""
        dev = self._find_dev(vendor_id, product_id)
        if not dev:
            msg = ('Cannot find USB device with VendorID=0x%04x '
                   'ProductID=0x%04x' % (vendor_id, product_id))
            log.critical(msg)
            raise weewx.WeeWxIOError('Unable to find station on USB')

        try:
            handle = dev.open()
        except usb.USBError as exception:
            log.critical('open_device() Unable to open USB interface. Reason: %s' %
                         exception)
            raise weewx.WakeupError(exception)
        except AttributeError as exception:
            log.critical('open_device() Device not specified. Reason: %s' %
                         exception)
            raise weewx.WakeupError(exception)

        # Detach a kernel driver only when the backend supports this method.
        try:
            handle.detachKernelDriver(self.interface)
        except (usb.USBError, AttributeError):
            pass

        # A previous process may have exited without releasing the interface.
        try:
            self._release_interface(handle, self.interface)
        except (usb.USBError, AttributeError):
            pass

        try:
            handle.claimInterface(self.interface)
        except usb.USBError as exception:
            log.critical('open_device() Unable to claim USB interface. Reason: %s' %
                         exception)
            raise weewx.WakeupError(exception)

        self.dev = dev
        self.handle = handle
        self._trace('EVENT', 'usb_open', vendor_id='0x%04x' % vendor_id,
                    product_id='0x%04x' % product_id,
                    interface=self.interface,
                    in_endpoint='0x%02x' % self.in_endpoint,
                    device=getattr(dev, 'filename', None))
        log.info('Opened WMR200 USB device VendorID=0x%04x ProductID=0x%04x' %
                 (vendor_id, product_id))

    def open_device(self, vendor_id, product_id):
        """Open the station and retain its IDs for later recovery."""
        self.vendor_id = vendor_id
        self.product_id = product_id
        with self._io_lock:
            self._open_device_unlocked(vendor_id, product_id)

    def _close_device_unlocked(self):
        """Release the current interface. Caller must hold _io_lock."""
        handle = self.handle
        self.handle = None
        self.dev = None
        if handle is None:
            return
        self._trace('EVENT', 'usb_close', interface=self.interface)
        try:
            self._release_interface(handle, self.interface)
        except (usb.USBError, AttributeError) as exception:
            log.warning('close_device() Unable to release USB interface. Reason: %s' %
                        exception)

    def close_device(self):
        """Release the USB interface and discard the current handle."""
        with self._io_lock:
            self._close_device_unlocked()

    def reopen_device(self, reason):
        """Release, refind, and reclaim the console after a hard USB stall."""
        if self.vendor_id is None or self.product_id is None:
            raise weewx.WeeWxIOError('USB device IDs are not available for reopen')

        with self._io_lock:
            self._trace('EVENT', 'usb_reopen_begin', reason=reason,
                        reopen_count=self.reopen_count)
            log.warning('Reopening WMR200 USB device after %s' % reason)
            self._close_device_unlocked()
            time.sleep(self.reopen_delay)
            self._open_device_unlocked(self.vendor_id, self.product_id)
            self.reopen_count += 1
            self._trace('EVENT', 'usb_reopen_ok', reason=reason,
                        reopen_count=self.reopen_count)
            log.warning('WMR200 USB device reopened successfully; count=%d' %
                        self.reopen_count)

    def _clear_input_halt(self):
        """Clear a genuine stall on the interrupt IN endpoint."""
        with self._io_lock:
            if not self.handle:
                return
            try:
                self.handle.clearHalt(self.in_endpoint)
                log.warning('Cleared USB halt on interrupt endpoint 0x%02x' %
                            self.in_endpoint)
            except (usb.USBError, AttributeError) as exception:
                log.warning('Unable to clear USB halt on endpoint 0x%02x: %s' %
                            (self.in_endpoint, exception))

    def read_device(self):
        """Read and validate one HID report from the console.

        Errno 110 and backend-specific "no data" messages are normal polling
        timeouts. EPIPE on the interrupt endpoint is retried, then recovered by
        reopening the handle when configured.
        """
        report = None
        retries = max(1, int(self.read_retries))

        for attempt in range(1, retries + 1):
            try:
                with self._io_lock:
                    if not self.handle:
                        raise weewx.WeeWxIOError(
                            'read_device() No USB handle for usb_device Read')
                    report = self.handle.interruptRead(
                        self.in_endpoint,
                        _WMR200_USB_FRAME_SIZE,
                        max(1, int(round(float(self.timeout_read) * 1000))))

                if not report:
                    self.last_read_status = 'timeout'
                    self._trace('RX', 'usb_read_empty',
                                endpoint='0x%02x' % self.in_endpoint)
                    return []

                valid_count = int(report[0])
                available_count = len(report) - 1
                if valid_count > available_count:
                    self.malformed_report_count += 1
                    self.stream_gap_count += 1
                    self.last_read_status = 'stream_gap'
                    self.last_stream_gap_reason = (
                        'HID count exceeds payload: valid=%d available=%d' %
                        (valid_count, available_count)
                    )
                    self._trace(
                        'RX', 'usb_read_malformed', data=report,
                        valid_count=valid_count,
                        available_count=available_count,
                        stream_gap_count=self.stream_gap_count,
                        classification='hid_count_exceeds_payload',
                        impact='protocol_byte_stream_discontinuity',
                        action='drop_hid_report_and_resync_protocol')
                    log.warning(
                        'read_device() Dropping malformed USB report and '
                        'requesting protocol resync: valid=%d available=%d '
                        'gap=%d report=%s' %
                        (valid_count, available_count,
                         self.stream_gap_count, report))
                    return []

                self.byte_cnt_rd += len(report)
                self._mark_successful_read()
                payload = report[1:valid_count + 1]
                self.last_read_status = 'ok' if payload else 'empty'
                self._trace('RX', 'usb_interrupt_read', data=report,
                            endpoint='0x%02x' % self.in_endpoint,
                            valid_count=valid_count,
                            payload_hex=DeveloperTrace._hex(payload))
                if DEBUG_READS:
                    log.debug('read_device(): %s' %
                              ' '.join('%02x' % byte for byte in payload))
                return payload

            except usb.USBError as exception:
                number = self._error_number(exception)
                if self._is_timeout(exception):
                    # gp9: this is only a short scheduling slice timeout. It
                    # releases _io_lock so a pending D0/DA control write can
                    # run promptly. Health/recovery timeout accounting remains
                    # based on 15-second logical silence intervals.
                    self.last_read_status = 'poll_timeout'
                    now_monotonic = time.monotonic()
                    self.read_poll_timeout_count += 1
                    silence_seconds = self._seconds_since_last_success(
                        now_monotonic)
                    logical_interval = max(
                        1.0, float(self.logical_timeout_interval))
                    logical_level = int(silence_seconds // logical_interval)

                    self._trace(
                        'RX', 'usb_poll_timeout',
                        classification='short_interrupt_read_slice_timeout',
                        errno=number,
                        poll_slice_seconds=round(float(self.timeout_read), 3),
                        poll_timeout_total=self.read_poll_timeout_count,
                        logical_timeout_seconds=logical_interval,
                        logical_timeout_level=logical_level,
                        seconds_since_last_success=round(silence_seconds, 3),
                        last_success_utc=self._last_success_utc,
                        reason=str(exception),
                        impact='none_by_itself',
                        action='release_usb_lock_and_continue_polling')

                    # Promote only newly crossed logical timeout boundaries.
                    # For example, seven 2-second poll slices are still only
                    # one 15-second communication timeout.
                    if logical_level > self._logical_timeout_level:
                        previous_level = self._logical_timeout_level
                        delta = logical_level - previous_level
                        if previous_level == 0:
                            self.timeout_burst_count += 1
                        self.read_timeout_count += delta
                        self._logical_timeout_level = logical_level
                        self.consecutive_read_timeouts = logical_level
                        self.max_consecutive_read_timeouts = max(
                            self.max_consecutive_read_timeouts, logical_level)
                        severity, health_state = self._timeout_health()

                        self._trace(
                            'RX', 'usb_read_timeout',
                            severity=severity,
                            health_state=health_state,
                            classification='logical_communication_silence',
                            errno=number,
                            timeout_seconds=logical_interval,
                            poll_slice_seconds=round(
                                float(self.timeout_read), 3),
                            poll_timeout_total=self.read_poll_timeout_count,
                            timeout_total=self.read_timeout_count,
                            timeout_bursts=self.timeout_burst_count,
                            timeout_consecutive=self.consecutive_read_timeouts,
                            max_consecutive_timeouts=self.max_consecutive_read_timeouts,
                            seconds_since_last_success=round(
                                silence_seconds, 3),
                            last_success_utc=self._last_success_utc,
                            reason=str(exception),
                            impact=('not_confirmed_packet_loss'
                                    if severity == 'INFO'
                                    else 'possible_data_gap'),
                            action='request_heartbeat_and_continue_polling')

                        if self.consecutive_read_timeouts in (
                                self.timeout_warn_consecutive,
                                self.timeout_error_consecutive):
                            self._trace_health_snapshot(
                                trigger='timeout_threshold', force=True,
                                now_monotonic=now_monotonic)
                        else:
                            self._trace_health_snapshot(
                                trigger='timeout',
                                now_monotonic=now_monotonic)

                        log.debug(
                            'Logical USB silence timeout level=%d total=%d '
                            'silence=%.1fs (poll slices=%d)' %
                            (logical_level, self.read_timeout_count,
                             silence_seconds, self.read_poll_timeout_count))
                    return []

                if self._is_pipe_stall(exception):
                    self.read_pipe_stall_count += 1
                    self._trace('RX', 'usb_read_pipe_stall',
                                errno=number, attempt=attempt,
                                retries=retries, reason=str(exception))
                    log.warning('read_device() USB pipe stall errno=%s ' \
                                'attempt=%d/%d: %s' %
                                (number, attempt, retries, exception))
                    self._clear_input_halt()
                    if attempt < retries:
                        time.sleep(self.retry_delay)
                        continue
                    if self.reopen_on_failure:
                        self.reopen_device('repeated interrupt-read pipe stalls')
                        self.last_read_status = 'recovered'
                        return []

                self.last_read_status = 'error'
                self._trace('RX', 'usb_read_error', errno=number,
                            reason=str(exception), attempt=attempt,
                            retries=retries)
                msg = ('read_device() USB error errno=%s reason=%s' %
                       (number, exception))
                log.error(msg)
                raise weewx.WeeWxIOError(msg)

            except (IndexError, TypeError, ValueError) as exception:
                self.malformed_report_count += 1
                self.stream_gap_count += 1
                self.last_read_status = 'stream_gap'
                self.last_stream_gap_reason = str(exception)
                self._trace(
                    'RX', 'usb_read_parse_error', data=report,
                    reason=str(exception),
                    stream_gap_count=self.stream_gap_count,
                    impact='protocol_byte_stream_discontinuity',
                    action='drop_hid_report_and_resync_protocol')
                log.warning(
                    'read_device() Dropping malformed report and requesting '
                    'protocol resync gap=%d report=%s: %s' %
                    (self.stream_gap_count, report, exception))
                return []

        return []

    def _control_write_once(self, buf, value):
        """Perform one serialized USB control transfer.

        Return the time spent waiting to acquire the shared PyUSB I/O lock.
        gp9 records this so heartbeat scheduling can be verified from a trace.
        """
        wait_started = time.monotonic()
        with self._io_lock:
            lock_wait_s = max(0.0, time.monotonic() - wait_started)
            if not self.handle:
                raise weewx.WeeWxIOError(
                    'write_device() No USB handle for usb_device Write')
            self.handle.controlMsg(
                usb.TYPE_CLASS + usb.RECIP_INTERFACE,  # requestType
                0x0000009,                             # request
                buf,
                value,                                 # value
                0x0000000,                             # index
                _WMR200_USB_RESET_TIMEOUT)             # timeout
            return lock_wait_s

    def write_device(self, buf):
        """Write a command with bounded retry and one controlled reopen."""
        # HID Set_Report wValue used by the original WMR200 driver.
        value = 0x00000220
        retries = max(1, int(self.write_retries))

        if DEBUG_WRITES:
            log.debug('write_device(): %s' % buf)

        last_exception = None
        for attempt in range(1, retries + 1):
            try:
                lock_wait_s = self._control_write_once(buf, value)
                self.byte_cnt_wr += len(buf)
                self._trace('TX', 'usb_control_write', data=buf,
                            attempt=attempt, retries=retries,
                            status='ok', value='0x%08x' % value,
                            lock_wait_s=round(lock_wait_s, 6))
                return lock_wait_s
            except usb.USBError as exception:
                last_exception = exception
                number = self._error_number(exception)
                self._trace('TX', 'usb_control_write', data=buf,
                            attempt=attempt, retries=retries,
                            status='error', errno=number,
                            reason=str(exception), value='0x%08x' % value)
                if self._is_pipe_stall(exception) or self._is_timeout(exception):
                    self.write_transient_error_count += 1
                    log.warning('write_device() transient USB error errno=%s '
                                'attempt=%d/%d: %s' %
                                (number, attempt, retries, exception))
                    if attempt < retries:
                        time.sleep(self.retry_delay)
                        continue
                    break

                msg = ('write_device() USB control message failed errno=%s '
                       'reason=%s' % (number, exception))
                log.error(msg)
                raise weewx.WeeWxIOError(msg)

        # Do not issue clearHalt(0x00): endpoint zero is the USB control
        # endpoint and a fresh SETUP transaction already clears a control
        # transfer stall. Reopening is the safer final recovery step.
        if self.reopen_on_failure:
            try:
                self.reopen_device('repeated control-transfer failures')
                lock_wait_s = self._control_write_once(buf, value)
                self.byte_cnt_wr += len(buf)
                self._trace('TX', 'usb_control_write', data=buf,
                            attempt='after_reopen', status='ok',
                            value='0x%08x' % value,
                            lock_wait_s=round(lock_wait_s, 6))
                log.warning('write_device() Command succeeded after USB reopen')
                return lock_wait_s
            except (usb.USBError, weewx.WeeWxIOError, weewx.WakeupError) as exception:
                last_exception = exception
                self._trace('TX', 'usb_control_write', data=buf,
                            attempt='after_reopen', status='error',
                            errno=self._error_number(exception),
                            reason=str(exception), value='0x%08x' % value)

        number = self._error_number(last_exception) if last_exception else None
        msg = ('write_device() Failed after %d attempts and recovery; '
               'errno=%s reason=%s' %
               (retries, number, last_exception))
        log.error(msg)
        raise weewx.WeeWxIOError(msg)

class Packet(object):
    """Top level class for all WMR200 packets.

    All wmr200 packets inherit from this class.  The process() method
    is used to provide useful data to the weewx engine.  Some packets
    require special processing due to discontinuities in the wmr200
    protocol."""
    pkt_cmd = 0
    pkt_name = 'AbstractPacket'
    pkt_len = 0
    pkt_id = 0
    def __init__(self, wmr200):
        """Initialize base elements of the packet parser."""
        # Keep reference to the wmr200 for any special considerations
        # or options.
        self.wmr200 = wmr200
        # Accumulated raw byte data from console.
        self._pkt_data = []
        # Record dictionary to pass to weewx engine.
        self._record = {}
        # Add the command byte as the first field
        self.append_data(self.pkt_cmd)
        # Packet identifier
        Packet.pkt_id += 1
        self.pkt_id = Packet.pkt_id

    def append_data(self, char):
        """Appends new data to packet buffer.

        Verifies that the size is a reasonable value.
        Upon startup or other times we can may get out
        of sync with the weather console."""
        self._pkt_data.append(char)
        if (len(self._pkt_data) == 2 and
            self._pkt_data[1] > _WMR200_MAX_PACKET_SIZE):
            raise weewx.WeeWxIOError('Max packet size exceeded')     

    def size_actual(self):
        """Size of bytes of data in packet received from console."""
        return len(self._pkt_data)

    def size_expected(self):
        """Expected size of packet from packet protocol field."""
        try:
            return self._pkt_data[1]
        except IndexError:
            log.error('Failed to extract size from packet')
            return 0

    def packet_complete(self):
        """Determines if packet is complete and ready for weewx engine
        processing.
        
        This method assumes the packet is at least 2 bytes long"""
        if self.size_actual() < 2:
            return False
        return self.size_actual() == self.size_expected()

    def packet_process(self):
        """Process the raw data and creates a record field."""
        # Convention is that this driver only works in metric units.
        self._record.update({'usUnits': weewx.METRIC})
        if DEBUG_PACKETS_RAW or DEBUG_PACKETS_COOKED:
            log.debug('Processing %s' % self.pkt_name)
        if self.pkt_len and self.pkt_len != self.size_actual():
            log.warning(('Unexpected packet size act:%d exp:%d' % (self.size_actual(), self.pkt_len)))
        # If applicable calculate time drift between packet and host.
        self.calc_time_drift()

    def packet_record(self):
        """Returns the dictionary of processed records for this packet."""
        return self._record

    def record_get(self, key):
        """Returns the record indexed by the key."""
        try:
            return self._record[key]
        except KeyError:
            log.error('Record get key not found in record key:%s' % key)

    def record_set(self, key, val):
        """Sets the record indexed by the key."""
        try:
            self._record[key] = val
        except KeyError:
            log.error('Record set key not found in record key:%s val:%s' % (key, val))

    def record_update(self, record):
        """Updates record dictionary with additional dictionary."""
        try:
            self._record.update(record)
        except (TypeError, KeyError):
            log.error('Record update failed to apply record:%s' % record)

    def _checksum_calculate(self):
        """Returns the calculated checksum of the current packet.
        
        If the entire packet has not been received will simply
        return the checksum of whatever data values exist in the packet."""
        try:
            cksum = 0
            # Checksum is last two bytes in packet.
            for byte in self._pkt_data[:-2]:
                cksum += byte
            return cksum

        except IndexError:
            msg = 'Packet too small to compute 16 bit checksum'
            raise WMR200ProtocolError(msg)

    def _checksum_field(self):
        """Returns the checksum field of the current packet.

        If the entire packet has not been received will simply
        return the last two bytes which are unlikely checksum values."""
        try:
            return (self._pkt_data[-1] << 8) | self._pkt_data[-2]
        except IndexError:
            msg = 'Packet too small to contain 16 bit checksum'
            raise WMR200ProtocolError(msg)

    def verify_checksum(self):
        """Verifies packet for checksum correctness.
        
        Raises exception upon checksum failure unless configured to drop."""
        if self._checksum_calculate() != self._checksum_field():
            msg = ('Checksum miscompare act:0x%04x exp:0x%04x' % 
                   (self._checksum_calculate(), self._checksum_field()))
            log.error(self.to_string_raw('%s packet:' % msg))
            if self.wmr200.ignore_checksum:
                raise WMR200PacketParsingError(msg)
            raise weewx.CRCError(msg)

        # Debug test to force checksum recovery testing.
        if DEBUG_CHECKSUM and (self.pkt_id % DEBUG_CHECKSUM) == 0:
            raise weewx.CRCError('Debug forced checksum error')

    @staticmethod
    def timestamp_host():
        """Returns the host epoch timestamp"""
        return int(time.time() + 0.5)

    def timestamp_record(self):
        """Returns the epoch timestamp in the record."""
        try:
            return self._record['dateTime']
        except KeyError:
            msg = 'timestamp_record() Timestamp not set in record'
            log.error(msg)
            raise weewx.ViolatedPrecondition(msg)

    def _timestamp_packet(self, pkt_data):
        """Pulls the epoch timestamp from the packet."""
        try:
            minute = pkt_data[0]
            hour = pkt_data[1]
            day = pkt_data[2]
            month = pkt_data[3]
            year = 2000 + pkt_data[4]
            return time.mktime((year, month, day, hour, minute,
                                0, -1, -1, -1))
        except IndexError:
            msg = ('Packet length too short to get timestamp len:%d'
                   % len(self._pkt_data))
            raise WMR200ProtocolError(msg)

        except (OverflowError, ValueError) as exception:
            msg = ('Packet timestamp with bogus fields min:%d hr:%d day:%d'
                   ' m:%d y:%d %s' % (pkt_data[0], pkt_data[1],
                   pkt_data[2], pkt_data[3], pkt_data[4], exception))
            raise WMR200PacketParsingError(msg)

    def timestamp_packet(self):
        """Pulls the epoch timestamp from the packet.  
        Must only be called by packets that have timestamps in the
        protocal packet."""
        return self._timestamp_packet(self._pkt_data[2:7])

    def calc_time_drift(self):
        """Calculate time drift between host and packet

        Not all packets have a live timestamp so must be implemented
        by the packet type."""
        pass

    def to_string_raw(self, out=''):
        """Returns raw string of this packet appended to optional
        input string"""
        for byte in self._pkt_data:
            out += '%02x ' % byte
        return out

    def print_cooked(self):
        """Debug method method to print the processed packet.
        
        Must be called after the Process() method."""
        try:
            out = ' Packet cooked: '
            out += 'id:%d ' % self.pkt_id
            out += '%s ' % self.pkt_name
            out += '%s ' % weeutil.weeutil.timestamp_to_string(
                self.timestamp_record())
            out += 'len:%d ' % self.size_actual()
            out += 'fields:%d ' % len(self._record)
            out += str(self._record)
            log.debug(out)
        except KeyError:
            msg = 'print_cooked() called before proper setup'
            log.error(msg)
            raise weewx.ViolatedPrecondition(msg)

class PacketLive(Packet):
    """Packets with live sensor data from console."""
    # Number of live packets received from console.
    pkt_rx = 0
    # Queue of processed packets to be delivered to weewx.
    pkt_queue = []
    def __init__(self, wmr200):
        super(PacketLive, self).__init__(wmr200)
        PacketLive.pkt_rx += 1

    @staticmethod
    def packet_live_data():
        """Yield live data packets to interface on the weewx engine."""
        return True

    @staticmethod
    def packet_archive_data():
        """Yield archived data packets to interface on the weewx engine."""
        return False

    def packet_process(self):
        """Returns a records field to be processed by the weewx engine."""
        super(PacketLive, self).packet_process()
        self._record.update({'dateTime': self.timestamp_live(), })

    def calc_time_drift(self):
        """Returns the difference between PC time and the packet timestamp.
        This value is approximate as all timestamps from a given archive
        interval will be the same while PC time marches onwards.
        Only done once upon first live packet received."""
        if self.wmr200.time_drift is None:
            self.wmr200.time_drift = self.timestamp_host() \
                - self.timestamp_packet()
            log.info('Time drift between host and console in seconds:%d' % self.wmr200.time_drift)

    def timestamp_live(self):
        """Returns the timestamp from a live packet.

        Caches the last live timestamp to add to packets that do 
        not provide timestamps."""
        if self.wmr200.use_pc_time:
            self.wmr200.last_time_epoch = self.timestamp_host()
        else:
            self.wmr200.last_time_epoch = self.timestamp_packet()
        return self.wmr200.last_time_epoch

class PacketArchive(Packet):
    """Packets with archived sensor data from console."""
    # Number of archive packets received from console.
    pkt_rx = 0
    # Queue of processed packets to be delivered to weewx.
    pkt_queue = []
    def __init__(self, wmr200):
        super(PacketArchive, self).__init__(wmr200)
        PacketArchive.pkt_rx += 1

    @staticmethod
    def packet_live_data():
        """Yield live data packets to interface on the weewx engine."""
        return False

    @staticmethod
    def packet_archive_data():
        """Yield archived data packets to interface on the weewx engine."""
        return True

    def packet_process(self):
        """Returns a records field to be processed by the weewx engine."""
        super(PacketArchive, self).packet_process()
        # If we need to adjust the timestamp if pc time is set we will do it
        # later
        self._record.update({'dateTime': self.timestamp_packet(), })
        # Archive packets have extra field indicating interval time.
        self._record.update({'interval':
                             int(self.wmr200.archive_interval / 60.0), })

    def timestamp_adjust_drift(self):
        """Archive records may need time adjustment when using PC time."""
        try:
            log.info(('Using pc time adjusting archive record time by %d sec %s => %s'
                      % (self.wmr200.time_drift,
                         weeutil.weeutil.timestamp_to_string(self.timestamp_record()),
                         weeutil.weeutil.timestamp_to_string(self.timestamp_record() + int(self.wmr200.time_drift)))))
            self._record['dateTime'] += int(self.wmr200.time_drift)
        except TypeError:
            log.error('timestamp_adjust_drift() called with invalid time drift')

class PacketControl(Packet):
    """Packets with protocol control info from console."""
    # Number of control packets received from console.
    pkt_rx = 0
    def __init__(self, wmr200):
        super(PacketControl, self).__init__(wmr200)
        PacketControl.pkt_rx += 1

    @staticmethod
    def packet_live_data():
        """Yield live data packets to interface on the weewx engine."""
        return False

    @staticmethod
    def packet_archive_data():
        """Yield archived data packets to interface on the weewx engine."""
        return False

    def size_expected(self):
        """Control packets do not have length field and are only one byte."""
        return 1

    def verify_checksum(self):
        """This packet does not have a checksum."""
        pass

    def packet_complete(self):
        """Determines if packet is complete and ready for weewx engine
        processing."""
        if self.size_actual() == 1:
            return True
        return False

    def packet_process(self):
        """Returns a records field to be processed by the weewx engine.
        
        This packet isn't really passed up to weewx but is assigned a
        timestamp for completeness."""
        self._record.update({'dateTime': self.timestamp_host(), })

    def print_cooked(self):
        """Print the processed packet.
        
        This packet consists of a single byte and thus not much to print."""
        out = ' Packet cooked: '
        out += '%s ' % self.pkt_name
        log.debug(out)

class PacketArchiveReady(PacketControl):
    """Packet parser for control command acknowledge."""
    pkt_cmd = 0xd1
    pkt_name = 'CmdAck'
    pkt_len = 1
    def __init__(self, wmr200):
        super(PacketArchiveReady, self).__init__(wmr200)

    def packet_process(self):
        """Handle archive-ready according to the active driver mode."""
        super(PacketArchiveReady, self).packet_process()
        self.wmr200.handle_archive_ready(packet_id=self.pkt_id)

class PacketArchiveData(PacketArchive):
    """Packet parser for archived data."""
    pkt_cmd = 0xd2
    pkt_name = 'Archive Data'

    # Initial console rain total value since 2007-1-1.
    rain_total_last = None

    def __init__(self, wmr200):
        super(PacketArchiveData, self).__init__(wmr200)

    def packet_process(self):
        """Returns a records field to be processed by the weewx engine."""
        super(PacketArchiveData, self).packet_process()
        try:
            self._record.update(decode_rain(self,     self._pkt_data[ 7:20]))
            self._record.update(decode_wind(self,     self._pkt_data[20:27]))
            self._record.update(decode_uvi(self,      self._pkt_data[27:28]))
            self._record.update(decode_pressure(self, self._pkt_data[28:32]))
            # Number of sensors starting at zero inclusive.
            num_sensors = self._pkt_data[32]

            for i in range(num_sensors + 1):
                base = 33 + i * 7
                self._record.update(decode_temp(self,
                                                self._pkt_data[base:base + 7]))
        except IndexError:
            msg = ('%s decode index failure' % self.pkt_name)
            raise WMR200ProtocolError(msg)

        # During startup archive recovery request the next historical
        # record. During normal LIVE mode gp9 must not accidentally start or
        # continue an archive-drain cycle.
        self.wmr200.handle_archive_data_processed(self)

        if DEBUG_PACKETS_ARCHIVE:
            log.debug('  Archive packet num_temp_sensors:%d' % num_sensors)

    def timestamp_last_rain(self):
        """Pulls the epoch timestamp from the packet.  
        Returns the epoch time since last accumualted rainfall."""
        return self._timestamp_packet(self._pkt_data[15:20])

def decode_wind(pkt, pkt_data):
    """Decode the wind portion of a wmr200 packet."""
    try:
        # Low byte of gust speed in 0.1 m/s.
        gust_speed = ((((pkt_data[3]) & 0x0f) << 8)
                      | pkt_data[2]) / 10.0
        # High nibble is low nibble of average speed.
        # Low nibble of high byte and high nibble of low byte
        # of average speed. Value is in 0.1 m/s
        avg_speed = ((pkt_data[3] >> 4)
                     | (pkt_data[4] << 4)) / 10.0
        # Wind direction in steps of 22.5 degrees.
        # 0 is N, 1 is NNE and so on. See WIND_DIR_MAP for complete list.
        dir_deg = (pkt_data[0] & 0x0f) * 22.5

        # Windchill temperature. The value is in degrees F.
        # Set default to no windchill as it may not exist.
        # Convert to metric for weewx presentation.
        windchill = None
        if pkt_data[6] != 0x20:
            if pkt_data[6] & 0x10:
                # Think it's a flag of some sort
                pass
            elif pkt_data[6] != 0x80:
                windchill = (((pkt_data[6] << 8) | pkt_data[5]) - 320) \
                        * (5.0 / 90.0)
            elif pkt_data[6] & 0x80:
                windchill = ((((pkt_data[5]) * -1) - 320) * (5.0 / 90.0))

        # The console returns wind speeds in m/s. weewx requires
        # kph, so the speeds needs to be converted.
        record = {'wind_speed': avg_speed * 3.60,
                  'wind_gust': gust_speed * 3.60,
                  'wind_dir': dir_deg,
                  'windchill': windchill}
        # Sometimes the station emits a wind gust that is less than the
        # average wind.  weewx requires kph, so the result needs to be 
        # converted.
        if gust_speed < avg_speed:
            record['wind_gust'] = None

        if DEBUG_PACKETS_WIND:
            log.debug('  Wind Dir: %s' % (WIND_DIR_MAP[pkt_data[0] & 0x0f]))
            log.debug('  Gust: %.1f m/s Wind:%.1f m/s' % (gust_speed, avg_speed))
            if windchill is not None:
                log.debug('  Windchill: %.1f C' % windchill)
        return record

    except IndexError:
        msg = ('%s decode index failure' % pkt.pkt_name)
        raise WMR200ProtocolError(msg)

class PacketWind(PacketLive):
    """Packet parser for wind."""
    pkt_cmd = 0xd3
    pkt_name = 'Wind'
    pkt_len = 0x10
    def __init__(self, wmr200):
        super(PacketWind, self).__init__(wmr200)

    def packet_process(self):
        """Decode a wind packet. Wind speed will be in kph

        Returns a packet that can be processed by the weewx engine."""
        super(PacketWind, self).packet_process()
        self._record.update(decode_wind(self, self._pkt_data[7:14]))

def decode_rain(pkt, pkt_data):
    """Decode the rain portion of a wmr200 packet."""
    try:
        # Bytes 0 and 1: high and low byte encode the current rainfall rate
        # in 0.01 in/h.  Convert into metric.
        rain_rate = (((pkt_data[1] & 0x0f) << 8) | pkt_data[0]) / 100.0 * 2.54
        # Bytes 2 and 3: high and low byte encode rain of the last hour in 0.01in
        # Convert into metric.
        rain_hour = ((pkt_data[3] << 8) | pkt_data[2]) / 100.0 * 2.54
        # Bytes 4 and 5: high and low byte encode rain of the last 24 hours, 
        # excluding the current hour, in 0.01in
        # Convert into metric.
        rain_day = ((pkt_data[5] << 8) | pkt_data[4]) / 100.0 * 2.54
        # Bytes 6 and 7: high and low byte encode the total rainfall in 0.01in.
        # Convert into metric.
        rain_total = ((pkt_data[7] << 8) | pkt_data[6]) / 100.0 * 2.54

        record = {'rain_rate': rain_rate,
                  'rain_hour': rain_hour,
                  'rain_24': rain_day + rain_hour,
                  'rain_total': rain_total}

        if DEBUG_PACKETS_RAIN:
            try:
                formatted = ["0x%02x" % x for x in pkt_data]
                log.debug('  Rain packets:' + ', '.join(formatted))
                log.debug('  Rain rate:%.02f; hour_rain:%.02f; day_rain:%.02f' % (rain_rate, rain_hour, rain_day))
                log.debug('  Total rain_total:%.02f', rain_total)
                log.debug('  Last rain %s' % weeutil.weeutil.timestamp_to_string(pkt.timestamp_last_rain()))
            except Exception:
                pass

        return record

    except IndexError:
        msg = ('%s decode index failure' % pkt.pkt_name)
        raise WMR200ProtocolError(msg)


def adjust_rain(pkt, packet):
    """Calculate rainfall per poll interval.
    Because the WMR does not offer anything like bucket tips, we must
    calculate it by looking for the change in total rain.
    After driver startup we need to initialize the total rain presented 
    by the console.
      There are two different rain total last values kept.  One for archive
    data and one for live loop data.  They are addressed using a static
    variable within the scope of the respective class name."""
    record = {}

    # Get the total current rain field from the console.
    rain_total = pkt.record_get('rain_total')

    # Calculate the amount of rain occurring for this interval.
    try:
        rain_interval = rain_total - packet.rain_total_last
    except TypeError:
        rain_interval = 0.0

    record['rain'] = rain_interval
    record['rain_total_last'] = packet.rain_total_last

    try:
        log.debug('  adjust_rain rain_total:%.02f %s.rain_total_last:%.02f rain_interval:%.02f'
                  % (rain_total, packet.pkt_name, packet.rain_total_last, rain_interval))
    except TypeError:
        log.debug('  Initializing %s.rain_total_last to %.02f' % (packet.pkt_name, rain_total))

    packet.rain_total_last = rain_total

    return record

class PacketRain(PacketLive):
    """Packet parser for rain."""
    pkt_cmd = 0xd4
    pkt_name = 'Rain'
    pkt_len = 0x16

    # Initial console rain total value since 2007-1-1.
    rain_total_last = None

    def __init__(self, wmr200):
        super(PacketRain, self).__init__(wmr200)

    def packet_process(self):
        """Returns a packet that can be processed by the weewx engine."""
        super(PacketRain, self).packet_process()
        self._record.update(decode_rain(self, self._pkt_data[7:20]))
        self._record.update(adjust_rain(self, PacketRain))

    def timestamp_last_rain(self):
        """Pulls the epoch timestamp from the packet.  
        Returns the epoch time since last accumualted rainfall."""
        return self._timestamp_packet(self._pkt_data[15:20])

def decode_uvi(pkt, pkt_data):
    """Decode the uvi portion of a wmr200 packet."""
    try:
        uv = pkt_data[0] & 0x0f
        record = {'uv': uv if uv != 0xff else None}

        if DEBUG_PACKETS_UVI:
            log.debug("  UV index:%s\n" % record['uv'])
        return record

    except IndexError:
        msg = ('%s index decode index failure' % pkt.pkt_name)
        raise WMR200ProtocolError(msg)


class PacketUvi(PacketLive):
    """Packet parser for ultra violet sensor."""
    pkt_cmd = 0xd5
    pkt_name = 'UVI'
    pkt_len = 0x0a
    def __init__(self, wmr200):
        super(PacketUvi, self).__init__(wmr200)

    def packet_process(self):
        """Returns a packet that can be processed by the weewx engine."""
        super(PacketUvi, self).packet_process()
        self._record.update(decode_uvi(self, self._pkt_data[7:8]))

def decode_pressure(pkt, pkt_data):
    """Decode the pressure portion of a wmr200 packet."""
    try:
        # Low byte of pressure. Value is in hPa.
        # High nibble is forecast
        # Low nibble is high byte of pressure.
        # Unfortunately, we do not know if this is MSLP corrected pressure,
        # or "gauge" pressure. We will assume the latter.
        pressure = float(((pkt_data[1] & 0x0f) << 8) | pkt_data[0])
        forecast = (pkt_data[1] >> 4) & 0x7

        # Similar to bytes 0 and 1, but altitude corrected
        # pressure. Upper nibble of byte 3 is still unknown. Seems to
        # be always 3.
        altimeter = float(((pkt_data[3] & 0x0f) << 8) | pkt_data[2])
        unknown_nibble = (pkt_data[3] >> 4)

        record = {'pressure': pressure,
                  'altimeter': altimeter,
                  'forecast_icon': forecast}

        if DEBUG_PACKETS_PRESSURE:
            log.debug('  Forecast: %s' % FORECAST_MAP[forecast])
            log.debug('  Raw pressure: %.02f hPa' % pressure)
            if unknown_nibble != 3:
                log.debug('  Pressure unknown nibble: 0x%x' % unknown_nibble)
            log.debug('  Altitude corrected pressure: %.02f hPa console' % altimeter)
        return record

    except IndexError:
        msg = ('%s index decode index failure' % pkt.pkt_name)
        raise WMR200ProtocolError(msg)


class PacketPressure(PacketLive):
    """Packet parser for barometer sensor."""
    pkt_cmd = 0xd6
    pkt_name = 'Pressure'
    pkt_len = 0x0d
    def __init__(self, wmr200):
        super(PacketPressure, self).__init__(wmr200)

    def packet_process(self):
        """Returns a packet that can be processed by the weewx engine."""
        super(PacketPressure, self).packet_process()
        self._record.update(decode_pressure(self, self._pkt_data[7:11]))


def decode_temp(pkt, pkt_data):
    """Decode the temperature portion of a wmr200 packet."""
    try:
        record = {}
        # The historic data can contain data from multiple sensors. I'm not
        # sure if the 0xD7 frames can do too. I've never seen a frame with
        # multiple sensors. But historic data bundles data for multiple
        # sensors.
        # Byte 0: low nibble contains sensor ID. 0 for base station.
        sensor_id = pkt_data[0] & 0x0f
        # '00 Temp steady
        # '01 Temp rising 
        # '10 Temp falling 
        temp_trend = (pkt_data[0] >> 6) & 0x3
        # '00 Humidity steady
        # '01 Humidity rising 
        # '10 Humidity falling 
        hum_trend = (pkt_data[0] >> 4) & 0x3

        # The high nible contains the sign indicator.
        # The low nibble is the high byte of the temperature.
        # The low byte of the temperature. The value is in 1/10
        # degrees centigrade.
        temp = (((pkt_data[2] & 0x0f) << 8) | pkt_data[1]) / 10.0
        if pkt_data[2] & 0x80:
            temp *= -1

        # The humidity in percent.
        humidity = pkt_data[3]

        # The first high nibble contains the sign indicator.
        # The first low nibble is the high byte of the temperature.
        # The second byte is low byte of the temperature. The value is in 1/10
        # degrees centigrade.
        dew_point = (((pkt_data[5] & 0x0f) << 8) | pkt_data[4]) / 10.0
        if pkt_data[5] & 0x80:
            dew_point *= -1
        # ignore the dewpoint and let weewx calculate it.

        # Heat index reported by console.
        heat_index = None
        if pkt_data[6] != 0:
            # For some strange reason it's reported in degF so convert
            # to metric.
            heat_index = (pkt_data[6] - 32) / (9.0 / 5.0)
        record['heatindex_%d' % sensor_id] = heat_index

        record['temperature_%d' % sensor_id] = temp
        record['humidity_%d' % sensor_id] = humidity

        if DEBUG_PACKETS_TEMP:
            log.debug('  Temperature id:%d %.1f C trend: %s' % (sensor_id, temp, TRENDS[temp_trend]))
            log.debug('  Humidity id:%d %d%% trend: %s' % (sensor_id, humidity, TRENDS[hum_trend]))
            log.debug('  Dew point id:%d: %.1f C' % (sensor_id, dew_point))
            if heat_index is not None:
                log.debug('  Heat id:%d index:%d' % (sensor_id, heat_index))
        return record

    except IndexError:
        msg = ('%s index decode index failure' % pkt.pkt_name)
        raise WMR200ProtocolError(msg)


class PacketTemperature(PacketLive):
    """Packet parser for temperature and humidity sensor."""
    pkt_cmd = 0xd7
    pkt_name = 'Temperature'
    pkt_len = 0x10
    def __init__(self, wmr200):
        super(PacketTemperature, self).__init__(wmr200)

    def packet_process(self):
        """Returns a packet that can be processed by the weewx engine."""
        super(PacketTemperature, self).packet_process()
        self._record.update(decode_temp(self, self._pkt_data[7:14]))
        # Save the temp record for possible windchill calculation.
        self.wmr200.last_temp_record = self._record

class PacketStatus(PacketLive):
    """Packet parser for console sensor status."""
    pkt_cmd = 0xd9
    pkt_name = 'Status'
    pkt_len = 0x08
    def __init__(self, wmr200):
        super(PacketStatus, self).__init__(wmr200)

    def timestamp_live(self):
        """Return timestamp of packet.
        
        This packet does not have a timestamp so we just return the
        previous cached timestamp from the last live packet.
        Note: If there is no previous cached timestamp then we 
        return the initial PC timestamp.  This would occur quite early
        in the driver startup and this time may be quite out of
        sequence from the rest of the packets.  Another option would be
        to simply discard this status packet at this time."""
        return self.wmr200.last_time_epoch

    def packet_process(self):
        """Returns a packet that can be processed by the weewx engine.
        
        Not all console status aligns with the weewx API but we try
        to make it fit."""
        super(PacketStatus, self).packet_process()
        # Setup defaults as good status.
        self._record.update({'out_fault': 0,
                             'wind_fault': 0,
                             'uv_fault': 0,
                             'rain_fault': 0,
                             'clock_unsynchronized': 0,
                             'battery_status_out': 0,
                             'battery_status_wind': 0,
                             'battery_status_uv': 0,
                             'battery_status_rain': 0})
        # This information may be sent to the system log
        msg_status = []
        if self._pkt_data[2] & 0x02:
            msg_status.append('Temp outdoor sensor fault')
            self._record['out_fault'] = 1

        if self._pkt_data[2] & 0x01:
            msg_status.append('Wind sensor fault')
            self._record['wind_fault'] = 1

        if self._pkt_data[3] & 0x20:
            msg_status.append('UV Sensor fault')
            self._record['uv_fault'] = 1

        if self._pkt_data[3] & 0x10:
            msg_status.append('Rain sensor fault')
            self._record['rain_fault'] = 1

        if self._pkt_data[4] & 0x80:
            msg_status.append('Clock time unsynchronized')
            self._record['clock_unsynchronized'] = 1

        if self._pkt_data[4] & 0x02:
            msg_status.append('Temp outdoor sensor: Battery low')
            self._record['battery_status_out'] = 1

        if self._pkt_data[4] & 0x01:
            msg_status.append('Wind sensor: Battery low')
            self._record['battery_status_wind'] = 1

        if self._pkt_data[5] & 0x20:
            msg_status.append('UV sensor: Battery low')
            self._record['battery_status_uv'] = 1

        if self._pkt_data[5] & 0x10:
            msg_status.append('Rain sensor: Battery low')
            self._record['battery_status_rain'] = 1

        if self.wmr200.sensor_stat:
            while msg_status:
                msg = msg_status.pop(0)
                log.warning(msg)

        # Output packet to try to understand other fields.
        if DEBUG_PACKETS_STATUS:
            log.debug(self.to_string_raw(' Sensor packet:'))

    def calc_time_drift(self):
        """Returns the difference between PC time and the packet timestamp.
        This packet has no timestamp so cannot be used to calculate."""
        pass

class PacketEraseAcknowledgement(PacketControl):
    """Packet parser for archived data is ready to receive."""
    pkt_cmd = 0xdb
    pkt_name = 'Erase Acknowledgement'
    pkt_len = 0x01
    def __init__(self, wmr200):
        super(PacketEraseAcknowledgement, self).__init__(wmr200)


class PacketFactory(object):
    """Factory to create proper packet from first command byte from device."""
    def __init__(self, *subclass_list):
        self.subclass = dict((s.pkt_cmd, s) for s in subclass_list)
        self.skipped_bytes = 0

    def num_packets(self):
        """Returns the number of packets handled by the factory."""
        return len(self.subclass)

    def get_packet(self, pkt_cmd, wmr200):
        """Returns a protocol packet instance from initial packet command byte.
       
        Returns None if there was no mapping for the protocol command.

        Upon startup we may read partial packets. We need to resync to a
        valid packet command from the weather console device if we start
        reading in the middle of a previous packet. 
       
        We may also get out of sync during operation."""
        if pkt_cmd in self.subclass:
            if self.skipped_bytes:
                log.warning(('Skipped bytes before resync:%d' % self.skipped_bytes))
                self.skipped_bytes = 0
            return self.subclass[pkt_cmd](wmr200)
        self.skipped_bytes += 1
        return None


# Packet factory parser for each packet presented by weather console.
PACKET_FACTORY = PacketFactory(
    PacketArchiveReady,
    PacketArchiveData,
    PacketWind,
    PacketRain,
    PacketPressure,
    PacketUvi,
    PacketTemperature,
    PacketStatus,
    PacketEraseAcknowledgement,
)

# Count of restarts
STAT_RESTART = 0

class RequestLiveData(threading.Thread):
    """Watchdog thread to poke the console requesting live data.

    If the console does not receive a request or heartbeat periodically
    for live data then it automatically resets into archive mode."""
    def __init__(self, kwargs):
        super(RequestLiveData, self).__init__()
        self.wmr200 = kwargs['wmr200']
        self.poke_time = kwargs['poke_time']
        self.sock_rd = kwargs['sock_rd']
        self.daemon = True

        log.info(('Created watchdog thread to poke for live data every %d seconds') % self.poke_time)

    def run(self):
        """Periodically inform the main driver thread to request live data.

        When its time to shutdown this thread, the main thread will send any
        string across the socket.  This both wakes up this timer thread and
        also tells it to expire."""
        log.info('Started watchdog thread live data')
        while True:
            self.wmr200.ready_to_poke(True, reason='watchdog')
            main_thread_comm = \
                    select.select([self.sock_rd], [], [], self.poke_time)
            if main_thread_comm[0]:
                # Data is ready to read on socket to indicate thread teardown.
                buf = self.sock_rd.recv(4096)
                log.info('Watchdog received %s' % buf)
                break

        log.info('Watchdog thread exiting')


class PollUsbDevice(threading.Thread):
    """A thread continually polls for data with blocking read from a device.
    
    Some devices may overflow buffers if not drained within a timely manner.
    
    This thread will read block on the USB port and buffer data from the
    device for consumption."""
    def __init__(self, kwargs):
        super(PollUsbDevice, self).__init__()
        self.wmr200 = kwargs['wmr200']
        self.usb_device = self.wmr200.usb_device

        # Buffer list to read data from weather console
        self._buf = []
        # Lock to wrap around the buffer
        self._lock_poll = threading.Lock()
        # Conditional variable to gate thread after reset applied.
        # We don't want to read previous data, if any, until a reset
        # has been sent.
        self._cv_poll = threading.Condition()
        # Gates initial entry into reading from device
        self._ok_to_read = False
        self._fatal_error = None
        self.daemon = True
        log.info('Created USB polling thread to read block on device')

    def run(self):
        """Continuously drain the interrupt endpoint into a shared buffer."""
        log.info('USB polling device thread for live data launched')
        try:
            # Wait for the main thread to indicate it is safe to read.
            with self._cv_poll:
                while not self._ok_to_read:
                    self._cv_poll.wait()
            log.info('USB polling device thread signaled to start')

            # Read and discard the first report after a reset.
            _ = self.usb_device.read_device()
            read_reset_cnt = 0

            while self.wmr200.poll_usb_device_enable():
                buf = self.usb_device.read_device()
                if buf:
                    self._append_usb_device(buf)
                    read_reset_cnt = 0
                    continue

                read_status = self.usb_device.last_read_status

                # Preserve ordering: the marker is queued exactly between the
                # bytes received before and after the malformed HID report.
                if read_status == 'stream_gap':
                    self._append_usb_stream_gap(
                        self.usb_device.stream_gap_count,
                        self.usb_device.last_stream_gap_reason)
                    read_reset_cnt = 0
                    continue

                # Empty-but-valid reports and successful USB recovery are not
                # communication timeouts and must not trigger console resets.
                if read_status in ('empty', 'recovered'):
                    read_reset_cnt = 0
                    continue

                if read_status == 'poll_timeout':
                    # Short read slices merely release the USB lock. Escalate
                    # only when a full logical 15-second silence boundary has
                    # been crossed, preserving gp7/gp8 recovery thresholds.
                    logical_level = self.usb_device.consecutive_read_timeouts
                    if logical_level >= 1:
                        self.wmr200.ready_to_poke(
                            True, reason='usb_silence_timeout')

                    # gp7/gp8 reset after four consecutive 15-second timeouts.
                    # With short read slices that is still one reset after
                    # roughly 60 seconds of continuous communication silence.
                    next_reset_level = 4 * (read_reset_cnt + 1)
                    if logical_level >= next_reset_level:
                        self.reset_console()
                        read_reset_cnt += 1
                        if read_reset_cnt >= 2:
                            raise weewx.RetriesExceeded(
                                'Device unresponsive after multiple console resets')
                    continue

                # Unknown/no-data states retain the conservative gp8 behavior:
                # ask the main thread for a heartbeat but do not manufacture a
                # timeout counter from a short scheduling slice.
                self.wmr200.ready_to_poke(True, reason='usb_read_no_data')

        except Exception as exception:
            self._fatal_error = exception
            log.exception('USB polling thread terminated: %s' % exception)
        finally:
            log.info('USB polling device thread exiting')

    @property
    def fatal_error(self):
        """Return the exception that terminated the polling thread."""
        return self._fatal_error

    def _append_usb_device(self, buf):
        """Append data from USB device to the ordered shared queue."""
        with self._lock_poll:
            self._buf.append(buf)

    def _append_usb_stream_gap(self, gap_count, reason):
        """Queue an ordered marker for bytes lost in a malformed HID report."""
        marker = (_WMR200_USB_STREAM_GAP_MARKER,
                  int(gap_count),
                  str(reason or 'malformed USB report'))
        with self._lock_poll:
            self._buf.append(marker)

    def read_usb_device(self):
        """Reads the buffered USB device data.
        Called from main thread.

        Returns a list of bytes."""
        buf = []
        with self._lock_poll:
            if self._buf:
                buf = self._buf.pop(0)
        return buf

    def flush_usb_device(self):
        """Flush any previous USB device data.
        Called from main thread."""
        self._lock_poll.acquire()
        self._buf = []
        self._lock_poll.release()
        log.info('Flushed USB device')

    def reset_console(self):
        """Send a reset command and release the polling-thread gate."""
        buf = [0x20, 0x00, 0x08, 0x01, 0x00, 0x00, 0x00, 0x00]
        self.wmr200._developer_trace.event(
            'TX', 'protocol_reset', data=buf,
            command_name='console_reset')
        try:
            self.usb_device.write_device(buf)
        except weewx.WeeWxIOError as exception:
            msg = ('reset_console() Unable to send USB reset command: %s' %
                   exception)
            log.error(msg)
            raise weewx.WeeWxIOError(msg)

        with self._cv_poll:
            self._ok_to_read = True
            getattr(self._cv_poll, 'notify_all', self._cv_poll.notifyAll)()
        log.info('Reset console device')
        time.sleep(1)

    def notify(self):
        """Release the read gate. Retained for backward compatibility."""
        with self._cv_poll:
            getattr(self._cv_poll, 'notify_all', self._cv_poll.notifyAll)()

class WMR200(weewx.drivers.AbstractDevice):
    """Driver for the Oregon Scientific WMR200 station."""

    DEFAULT_MAP = {
        'altimeter': 'altimeter',
        'pressure': 'pressure',
        'windSpeed': 'wind_speed',
        'windDir': 'wind_dir',
        'windGust': 'wind_gust',
        'windBatteryStatus': 'battery_status_wind',
        'inTemp': 'temperature_0',
        'outTemp': 'temperature_1',
        'extraTemp1': 'temperature_2',
        'extraTemp2': 'temperature_3',
        'extraTemp3': 'temperature_4',
        'extraTemp4': 'temperature_5',
        'extraTemp5': 'temperature_6',
        'extraTemp6': 'temperature_7',
        'extraTemp7': 'temperature_8',
        'inHumidity': 'humidity_0',
        'outHumidity': 'humidity_1',
        'extraHumid1': 'humidity_2',
        'extraHumid2': 'humidity_3',
        'extraHumid3': 'humidity_4',
        'extraHumid4': 'humidity_5',
        'extraHumid5': 'humidity_6',
        'extraHumid6': 'humidity_7',
        'extraHumid7': 'humidity_8',
        'inHeatindex': 'heatindex_0',
        'heatindex': 'heatindex_1',
        'heatindex1': 'heatindex_2',
        'heatindex2': 'heatindex_3',
        'heatindex3': 'heatindex_4',
        'heatindex4': 'heatindex_5',
        'heatindex5': 'heatindex_6',
        'heatindex6': 'heatindex_7',
        'heatindex7': 'heatindex_8',
        'outTempBatteryStatus': 'battery_status_out',
        'rain': 'rain',
        'rainTotal': 'rain_total',
        'rainRate': 'rain_rate',
        'hourRain': 'rain_hour',
        'rain24': 'rain_24',
        'rainBatteryStatus': 'battery_status_rain',
        'UV': 'uv',
        'uvBatteryStatus': 'battery_status_uv',
        'windchill': 'windchill',
        'forecastIcon': 'forecast_icon',
        'outTempFault': 'out_fault',
        'windFault': 'wind_fault',
        'uvFault': 'uv_fault',
        'rainFault': 'rain_fault',
        'clockUnsynchronized': 'clock_unsynchronized'}

    def __init__(self, **stn_dict):
        """Initialize the wmr200 driver.
        
        NAMED ARGUMENTS:
        model: Which station model is this? [Optional]
        sensor_status: Print sensor faults or failures to the log. [Optional]
        use_pc_time: Use the console timestamp or the Pc. [Optional]
        erase_archive:  Erase archive upon startup.  [Optional]
        archive_interval: Time in seconds between intervals [Optional]
        archive_threshold: Max time in seconds between valid archive packets [Optional]
        ignore_checksum: Ignore checksum failures and drop packet.
        archive_startup: Time after startup to await archive data draining.

        --- User should not typically change anything below here ---

        vendor_id: The USB vendor ID for the WMR [Optional]
        product_id: The USB product ID for the WM [Optional]
        interface: The USB interface [Optional]
        in_endpoint: The IN USB endpoint used by the WMR [Optional]
        """
        super(WMR200, self).__init__()

        # Optional complete text log for this driver. It is asynchronous so
        # disk I/O never runs in the USB acquisition path.
        driver_file_log_enabled = weeutil.weeutil.tobool(
            stn_dict.get('driver_file_log', False))
        driver_file_log_path = stn_dict.get(
            'driver_file_log_path', '/var/log/weewx/wmr200-debug.log')
        driver_file_log_level = stn_dict.get('driver_file_log_level', 'DEBUG')
        driver_file_log_max_mb = int(stn_dict.get(
            'driver_file_log_max_mb', 10))
        driver_file_log_backups = int(stn_dict.get(
            'driver_file_log_backups', 4))
        self._driver_file_log = DriverFileLog(
            enabled=driver_file_log_enabled,
            path=driver_file_log_path,
            level=driver_file_log_level,
            max_mb=driver_file_log_max_mb,
            backups=driver_file_log_backups,
            queue_size=4096)

        log.info('driver version is %s' % DRIVER_VERSION)
        if self._driver_file_log.enabled:
            log.warning('WMR200 driver file log ENABLED: %s level=%s max_mb=%d backups=%d'
                        % (self._driver_file_log.path,
                           self._driver_file_log.level_name,
                           int(driver_file_log_max_mb),
                           int(driver_file_log_backups)))

        # User configurable options
        self._model = stn_dict.get('model', 'WMR200')

        # get default mapping, override with user-specified
        self._sensor_map = dict(self.DEFAULT_MAP)
        if 'sensor_map' in stn_dict:
            self._sensor_map.update(stn_dict['sensor_map'])
        log.info('sensor map is %s' % self._sensor_map)

        # Provide sensor faults in the log.
        self._sensor_stat = \
            weeutil.weeutil.tobool(stn_dict.get('sensor_status', True))

        # Use pc timestamps or weather console timestamps.
        self._use_pc_time = \
            weeutil.weeutil.tobool(stn_dict.get('use_pc_time', True))

        # Use archive data when possible.
        self._erase_archive = \
            weeutil.weeutil.tobool(stn_dict.get('erase_archive', False))

        # Archive interval in seconds.
        self._archive_interval = int(stn_dict.get('archive_interval', 60))
        if self._archive_interval not in [60, 300]:
            log.warning('Unverified archive interval:%d sec' % self._archive_interval)

        # Archive threshold in seconds between archive packets before dropping.
        self._archive_threshold = int(stn_dict.get('archive_threshold',
                                                   3600 * 24 * 7))

        # Ignore checksum errors.
        self._ignore_checksum = \
                weeutil.weeutil.tobool(stn_dict.get('ignore_checksum', False))

        # Archive startup time in seconds.
        self._archive_startup = int(stn_dict.get('archive_startup', 120))

        # Non-blocking developer trace. Enabled by default in this diagnostic build.
        developer_trace_enabled = weeutil.weeutil.tobool(
            stn_dict.get('developer_trace', True))
        developer_trace_path = stn_dict.get(
            'developer_trace_path',
            '/var/log/weewx/wmr200-developer-trace.jsonl')
        developer_trace_max_mb = int(stn_dict.get(
            'developer_trace_max_mb', 10))
        developer_trace_backups = int(stn_dict.get(
            'developer_trace_backups', 4))
        developer_trace_queue_size = int(stn_dict.get(
            'developer_trace_queue_size', 4096))
        developer_trace_include_timeouts = weeutil.weeutil.tobool(
            stn_dict.get('developer_trace_include_timeouts', True))
        developer_trace_include_packets = weeutil.weeutil.tobool(
            stn_dict.get('developer_trace_include_packets', True))

        self._developer_trace = DeveloperTrace(
            enabled=developer_trace_enabled,
            path=developer_trace_path,
            max_mb=developer_trace_max_mb,
            backups=developer_trace_backups,
            queue_size=developer_trace_queue_size,
            include_timeouts=developer_trace_include_timeouts,
            include_packets=developer_trace_include_packets)
        if not self._developer_trace.enabled:
            log.warning('WMR200 developer trace is DISABLED by configuration')
        else:
            log.info('WMR200 developer trace active at %s' %
                     self._developer_trace.path)
        self._developer_trace.event(
            'EVENT', 'driver_start', driver=DRIVER_NAME,
            version=DRIVER_VERSION, model=self._model,
            erase_archive=self._erase_archive,
            archive_interval=self._archive_interval,
            driver_file_log=self._driver_file_log.enabled,
            driver_file_log_path=self._driver_file_log.path)

        # Device specific hardware and USB recovery options.
        vendor_id = int(stn_dict.get('vendor_id', '0x0fde'), 0)
        product_id = int(stn_dict.get('product_id', '0xca01'), 0)
        interface = int(stn_dict.get('interface', 0))
        in_endpoint = int(stn_dict.get('IN_endpoint', usb.ENDPOINT_IN + 1))
        usb_write_retries = int(stn_dict.get(
            'usb_write_retries', _WMR200_USB_WRITE_RETRIES))
        usb_read_retries = int(stn_dict.get(
            'usb_read_retries', _WMR200_USB_READ_RETRIES))
        usb_retry_delay = float(stn_dict.get(
            'usb_retry_delay', _WMR200_USB_RETRY_DELAY))
        usb_reopen_on_failure = weeutil.weeutil.tobool(
            stn_dict.get('usb_reopen_on_failure', True))
        usb_timeout_warn_consecutive = int(stn_dict.get(
            'usb_timeout_warn_consecutive', 2))
        usb_timeout_error_consecutive = int(stn_dict.get(
            'usb_timeout_error_consecutive', 4))
        usb_health_interval = float(stn_dict.get(
            'usb_health_interval', 300))
        usb_read_slice_timeout = float(stn_dict.get(
            'usb_read_slice_timeout', _WMR200_USB_READ_DATA_INTERVAL))
        usb_logical_timeout_seconds = float(stn_dict.get(
            'usb_logical_timeout_seconds',
            _WMR200_USB_LOGICAL_TIMEOUT_INTERVAL))

        # Buffer of bytes read from weather console device.
        self._buf = []

        # Packet created from the buffer data read from the weather console
        # device.
        self._pkt = None

        # Protocol recovery counters.
        self.protocol_resync_count = 0
        self.checksum_drop_count = 0

        # gp9 protocol mode. Archive commands are now state-aware so a D1/D2
        # seen during normal LIVE operation cannot accidentally start an
        # archive-drain loop or grow PacketArchive.pkt_queue indefinitely.
        self._protocol_mode = 'initializing'
        self._archive_recovery_active = False
        self.archive_ready_while_live_count = 0
        self.archive_data_while_live_count = 0
        self.archive_data_dropped_while_live_count = 0

        # Setup the generator to get a byte stream from the console.
        self.gen_byte = self._generate_bytestream

        # Calculate time delta in seconds between host and console.
        self.time_drift = None

        # Create USB accessor to communiate with weather console device.
        self.usb_device = UsbDevice(trace=self._developer_trace)

        # Pass USB parameters to the USB device accessor.
        self.usb_device.in_endpoint = in_endpoint
        self.usb_device.interface = interface
        self.usb_device.write_retries = max(1, usb_write_retries)
        self.usb_device.read_retries = max(1, usb_read_retries)
        self.usb_device.retry_delay = max(0.0, usb_retry_delay)
        self.usb_device.reopen_on_failure = usb_reopen_on_failure
        self.usb_device.timeout_warn_consecutive = max(
            1, usb_timeout_warn_consecutive)
        self.usb_device.timeout_error_consecutive = max(
            self.usb_device.timeout_warn_consecutive + 1,
            usb_timeout_error_consecutive)
        self.usb_device.health_interval = max(30.0, usb_health_interval)
        self.usb_device.timeout_read = max(0.25, usb_read_slice_timeout)
        self.usb_device.logical_timeout_interval = max(
            self.usb_device.timeout_read, usb_logical_timeout_seconds)
        self._developer_trace.event(
            'CONFIG', 'usb_scheduler_config',
            read_slice_seconds=self.usb_device.timeout_read,
            logical_timeout_seconds=self.usb_device.logical_timeout_interval,
            timeout_warn_consecutive=self.usb_device.timeout_warn_consecutive,
            timeout_error_consecutive=self.usb_device.timeout_error_consecutive,
            heartbeat_interval_seconds=_WMR200_REQUEST_LIVE_DATA_INTERVAL)

        # Locate the weather console device on the USB bus.
#        if not self.usb_device.find_device(vendor_id, product_id):
#            log.critical('Unable to find device with VendorID=%04x ProductID=%04x' %
#                   (vendor_id, product_id))
#            raise weewx.WeeWxIOError("Unable to find USB device")

        # Open the weather console USB device for read and writes.
        self.usb_device.open_device(vendor_id, product_id)

        # Initialize watchdog to poke device to request live data stream.
        # gp9 retains when and why the request was raised so developer traces
        # can verify that USB reads no longer delay D0 for tens of seconds.
        self._rdy_to_poke = True
        self._poke_requested_monotonic = time.monotonic()
        self._poke_request_reason = 'startup'

        # Create the lock to sync between main thread and watchdog thread.
        self._poke_lock = threading.Lock()

        # Create a socket pair to communicate with the watchdog thread.
        (self.sock_rd, self.sock_wr) = \
                socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM, 0)

        # Create the watchdog thread to request live data.
        self._thread_watchdog = RequestLiveData(
            kwargs={'wmr200': self,
                    'poke_time': _WMR200_REQUEST_LIVE_DATA_INTERVAL,
                    'sock_rd': self.sock_rd})

        # Create the usb polling device thread.
        self._thread_usb_poll = PollUsbDevice(kwargs={'wmr200': self})

        # Start the usb polling device thread.
        self._poll_device_enable = True
        self._thread_usb_poll.start()

        # Send the device a reset
        self._thread_usb_poll.reset_console()
        self._thread_usb_poll.notify()

        # Start the watchdog for live data thread.
        self._thread_watchdog.start()

        # Not all packets from wmr200 have timestamps, yet weewx requires
        # timestamps on all packets pass up the stack.  So we will use the 
        # timestamp from the most recent packet, but still need to see an
        # initial timestamp, so we'll seed this with current PC time.
        self.last_time_epoch = int(time.time() + 0.5)

        # Restart counter when driver crashes and is restarted by the
        # weewx engine.
        global STAT_RESTART
        STAT_RESTART += 1
        if STAT_RESTART > 1:
            log.warning('Restart count: %d' % STAT_RESTART)

        # Reset any other state during startup or after a crash. Static
        # packet queues must never leak records from an older driver instance.
        PacketArchiveData.rain_total_last = None
        PacketArchive.pkt_queue[:] = []
        PacketLive.pkt_queue[:] = []

        # Debugging flags
        global DEBUG_WRITES
        DEBUG_WRITES = int(stn_dict.get('debug_writes', 0))
        global DEBUG_COMM
        DEBUG_COMM = int(stn_dict.get('debug_comm', 0))
        global DEBUG_CONFIG_DATA
        DEBUG_CONFIG_DATA = int(stn_dict.get('debug_config_data', 1))
        global DEBUG_PACKETS_RAW
        DEBUG_PACKETS_RAW = int(stn_dict.get('debug_packets_raw', 0))
        global DEBUG_PACKETS_COOKED
        DEBUG_PACKETS_COOKED = int(stn_dict.get('debug_packets_cooked', 0))
        global DEBUG_PACKETS_ARCHIVE
        DEBUG_PACKETS_ARCHIVE = int(stn_dict.get('debug_packets_archive', 0))
        global DEBUG_PACKETS_TEMP
        DEBUG_PACKETS_TEMP = int(stn_dict.get('debug_packets_temp', 0))
        global DEBUG_PACKETS_RAIN
        DEBUG_PACKETS_RAIN = int(stn_dict.get('debug_packets_rain', 0))
        global DEBUG_PACKETS_UVI
        DEBUG_PACKETS_UVI = int(stn_dict.get('debug_packets_uvi', 0))
        global DEBUG_PACKETS_WIND
        DEBUG_PACKETS_WIND = int(stn_dict.get('debug_packets_wind', 0))
        global DEBUG_PACKETS_STATUS
        DEBUG_PACKETS_STATUS = int(stn_dict.get('debug_packets_status', 0))
        global DEBUG_PACKETS_PRESSURE
        DEBUG_PACKETS_PRESSURE = int(stn_dict.get('debug_packets_pressure', 0))
        global DEBUG_CHECKSUM
        DEBUG_CHECKSUM = int(stn_dict.get('debug_checksum', 0))
        global DEBUG_MAPPING
        DEBUG_MAPPING = int(stn_dict.get('debug_mapping', 0))
        global DEBUG_READS
        DEBUG_READS = int(stn_dict.get('debug_reads', 0))

        if DEBUG_CONFIG_DATA:
            log.debug('Configuration setup')
            log.debug('  Log sensor faults: %s' % self._sensor_stat)
            log.debug('  Using PC Time: %s' % self._use_pc_time)
            log.debug('  Erase archive data: %s' % self._erase_archive)
            log.debug('  Archive interval: %d' % self._archive_interval)
            log.debug('  Archive threshold: %d' % self._archive_threshold)
            log.debug('  USB write retries: %d' % self.usb_device.write_retries)
            log.debug('  USB read retries: %d' % self.usb_device.read_retries)
            log.debug('  USB retry delay: %.3f' % self.usb_device.retry_delay)
            log.debug('  USB reopen on failure: %s' % self.usb_device.reopen_on_failure)
            log.debug('  USB read slice timeout: %.3f sec' % self.usb_device.timeout_read)
            log.debug('  USB logical timeout: %.3f sec' % self.usb_device.logical_timeout_interval)
            log.debug('  Developer trace: %s' % self._developer_trace.enabled)
            log.debug('  Developer trace path: %s' % self._developer_trace.path)
            log.debug('  Driver file log: %s' % self._driver_file_log.enabled)
            log.debug('  Driver file log path: %s' % self._driver_file_log.path)
            log.debug('  Driver file log level: %s' % self._driver_file_log.level_name)

    @property
    def hardware_name(self):
        """weewx api."""
        return self._model

    @property
    def sensor_stat(self):
        """Return if sensor status is enabled for device."""
        return self._sensor_stat

    @property
    def use_pc_time(self):
        """Flag to use pc time rather than weather console time."""
        return self._use_pc_time

    @property
    def archive_interval(self):
        """weewx api.  Time in seconds between archive intervals."""
        return self._archive_interval

    @property
    def ignore_checksum(self):
        """Flag to drop rather than fail on checksum errors."""
        return self._ignore_checksum

    def ready_to_poke(self, val, reason=None):
        """Set heartbeat request state and retain its scheduling latency."""
        with self._poke_lock:
            val = bool(val)
            if val:
                if not self._rdy_to_poke:
                    self._poke_requested_monotonic = time.monotonic()
                    self._poke_request_reason = reason or 'unspecified'
                elif self._poke_requested_monotonic is None:
                    self._poke_requested_monotonic = time.monotonic()
                    self._poke_request_reason = reason or 'unspecified'
                self._rdy_to_poke = True
            else:
                self._rdy_to_poke = False
                self._poke_requested_monotonic = None
                self._poke_request_reason = None

    def is_ready_to_poke(self):
        """Get info that device is ready to be poked."""
        with self._poke_lock:
            return self._rdy_to_poke

    def _poke_request_snapshot(self):
        """Return heartbeat request age/reason without holding the lock."""
        with self._poke_lock:
            requested = self._poke_requested_monotonic
            reason = self._poke_request_reason
        age = None
        if requested is not None:
            age = max(0.0, time.monotonic() - requested)
        return age, reason

    def poll_usb_device_enable(self):
        """The USB thread calls this to enable data reads from the console."""
        return self._poll_device_enable

    def _write_cmd(self, cmd):
        """Write a single command and preserve useful failure context.

        Return wall time spent dispatching the command. gp9 records both this
        and the lower-level USB lock wait so heartbeat latency is measurable.
        """
        buf = [0x01, cmd, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        command_names = {0xD0: 'heartbeat_live', 0xDA: 'request_archive',
                         0xDB: 'erase_archive', 0xDF: 'driver_shutdown'}
        self._developer_trace.event(
            'TX', 'protocol_command', data=buf,
            command='0x%02x' % cmd,
            command_name=command_names.get(cmd, 'unknown'),
            protocol_mode=self._protocol_mode)
        started = time.monotonic()
        try:
            lock_wait_s = self.usb_device.write_device(buf)
            elapsed_s = max(0.0, time.monotonic() - started)
            self._developer_trace.event(
                'TX', 'protocol_command_complete',
                command='0x%02x' % cmd,
                command_name=command_names.get(cmd, 'unknown'),
                protocol_mode=self._protocol_mode,
                elapsed_s=round(elapsed_s, 6),
                usb_lock_wait_s=(round(lock_wait_s, 6)
                                 if lock_wait_s is not None else None))
            return elapsed_s
        except weewx.WeeWxIOError as exception:
            msg = ('_write_cmd() Unable to send USB cmd=0x%02x: %s' %
                   (cmd, exception))
            log.error(msg)
            raise weewx.WeeWxIOError(msg)

    def _poke_console(self):
        """Send the D0 live heartbeat and record scheduling latency."""
        request_age_s, request_reason = self._poke_request_snapshot()
        dispatch_started = time.monotonic()
        self._developer_trace.event(
            'TX', 'heartbeat_dispatch',
            request_reason=request_reason,
            request_age_s=(round(request_age_s, 6)
                           if request_age_s is not None else None),
            protocol_mode=self._protocol_mode)
        write_elapsed_s = self._write_cmd(0xD0)
        total_dispatch_s = max(0.0, time.monotonic() - dispatch_started)

        self._developer_trace.event(
            'TX', 'heartbeat_sent',
            request_reason=request_reason,
            request_age_s=(round(request_age_s, 6)
                           if request_age_s is not None else None),
            write_elapsed_s=(round(write_elapsed_s, 6)
                             if write_elapsed_s is not None else None),
            dispatch_elapsed_s=round(total_dispatch_s, 6),
            protocol_mode=self._protocol_mode)

        if self._erase_archive:
            self._write_cmd(0xDB)
            self._erase_archive = False
            log.warning('Console archive erase command sent once at startup')

        # Reset the ready-to-poke flag only after a successful D0 write.
        self.ready_to_poke(False)
        if DEBUG_COMM:
            log.debug('Poked device for live data')

    def _set_protocol_mode(self, mode, reason=None):
        """Record protocol mode transitions used by archive state handling."""
        previous = getattr(self, '_protocol_mode', None)
        self._protocol_mode = str(mode)
        if previous != self._protocol_mode:
            self._developer_trace.event(
                'STATE', 'protocol_mode_change',
                previous_mode=previous,
                new_mode=self._protocol_mode,
                reason=reason)
            log.info('WMR200 protocol mode %s -> %s reason=%s' %
                     (previous, self._protocol_mode, reason))

    def handle_archive_ready(self, packet_id=None):
        """Handle D1 without allowing archive mode to leak into LIVE."""
        if self._archive_recovery_active:
            self._developer_trace.event(
                'ARCHIVE', 'archive_ready_during_recovery',
                packet_id=packet_id, protocol_mode=self._protocol_mode,
                action='request_next_archive_record')
            self.request_archive_data()
            return

        self.archive_ready_while_live_count += 1
        self._developer_trace.event(
            'ARCHIVE', 'archive_ready_while_live',
            packet_id=packet_id, protocol_mode=self._protocol_mode,
            total=self.archive_ready_while_live_count,
            action='do_not_request_archive_reassert_live')
        log.warning(
            'Received archive-ready D1 outside startup recovery; '
            'reasserting LIVE mode instead of requesting archive data')
        self.ready_to_poke(True, reason='archive_ready_while_live')

    def handle_archive_data_processed(self, pkt):
        """Request the next D2 only while startup recovery is active."""
        if self._archive_recovery_active:
            self.request_archive_data()
            return

        self.archive_data_while_live_count += 1
        record_ts = None
        try:
            record_ts = int(pkt.timestamp_record())
        except Exception:
            pass
        self._developer_trace.event(
            'ARCHIVE', 'archive_data_while_live',
            packet_id=getattr(pkt, 'pkt_id', None),
            record_ts=record_ts, protocol_mode=self._protocol_mode,
            total=self.archive_data_while_live_count,
            action='do_not_request_next_archive_reassert_live')
        self.ready_to_poke(True, reason='archive_data_while_live')

    def _drop_pending_archive_packets(self, reason):
        """Discard stale D2 objects that must never accumulate in LIVE."""
        dropped = len(PacketArchive.pkt_queue)
        if dropped:
            PacketArchive.pkt_queue[:] = []
            self.archive_data_dropped_while_live_count += dropped
            self._developer_trace.event(
                'ARCHIVE', 'archive_queue_purged',
                dropped=dropped, reason=reason,
                protocol_mode=self._protocol_mode,
                dropped_total=self.archive_data_dropped_while_live_count)
            log.warning('Purged %d stale archive packet(s) while entering LIVE mode'
                        % dropped)
        return dropped

    def _resync_protocol_stream(self, gap_count, reason):
        """Abandon an incomplete protocol packet after USB bytes were lost.

        The marker is ordered in the same queue as the USB payloads, so bytes
        received before the gap are handled before this reset and bytes received
        afterwards are scanned afresh by PACKET_FACTORY.
        """
        dropped_name = None
        dropped_id = None
        actual_size = 0
        expected_size = 0

        if self._pkt is not None:
            dropped_name = self._pkt.pkt_name
            dropped_id = self._pkt.pkt_id
            actual_size = self._pkt.size_actual()
            expected_size = self._pkt.size_expected()

        buffered_bytes = len(self._buf)
        self._pkt = None
        self._buf = []
        self.protocol_resync_count += 1

        self._developer_trace.event(
            'PACKET', 'protocol_stream_resync',
            gap_count=gap_count,
            reason=reason,
            dropped_packet_name=dropped_name,
            dropped_packet_id=dropped_id,
            dropped_actual_size=actual_size,
            dropped_expected_size=expected_size,
            discarded_buffered_bytes=buffered_bytes,
            resync_total=self.protocol_resync_count,
            action='scan_for_next_known_command')

        log.warning(
            'Protocol stream resync after malformed USB report: gap=%s '
            'packet=%s id=%s size=%d/%d buffered=%d reason=%s' %
            (gap_count, dropped_name, dropped_id, actual_size,
             expected_size, buffered_bytes, reason))

    def _generate_bytestream(self):
        """Yield an ordered byte stream and consume stream-gap markers.

        A malformed HID report means bytes are missing. The ordered marker
        abandons only the incomplete protocol packet; subsequent bytes are then
        scanned for the next known WMR200 command.
        """
        while True:
            # Drain bytes already fetched before reading a later queue item.
            while self._buf:
                yield self._buf.pop(0)

            item = self._thread_usb_poll.read_usb_device()

            if (isinstance(item, tuple) and len(item) >= 3 and
                    item[0] == _WMR200_USB_STREAM_GAP_MARKER):
                self._resync_protocol_stream(item[1], item[2])
                continue

            if item:
                self._buf.extend(item)
                continue

            # Return control so heartbeat and queued-packet work can continue.
            return

    def _poll_for_data(self):
        """Poll for data from the weather console device.
        
        Read a byte from the weather console.  If we are starting
        a new packet, get one using that byte from the packet factory.
        Otherwise add the byte to the current packet.
        Each USB packet may stradle a protocol packet so make sure
        we assign the data appropriately."""
        if not self._thread_usb_poll.is_alive():
            reason = self._thread_usb_poll.fatal_error
            msg = ('USB polling thread unexpectedly terminated; reason=%s' %
                   reason)
            log.error(msg)
            raise weewx.WeeWxIOError(msg)

        for byte in self.gen_byte():
            if self._pkt:
                self._pkt.append_data(byte)
            else:
                # This may return None if we are out of sync
                # with the console.
                self._pkt = PACKET_FACTORY.get_packet(byte, self)

            if self._pkt is not None and self._pkt.packet_complete():
                # If we have a complete packet then bail to handle it.
                return

        # Prevent busy loop by suspending process a bit to
        # wait for usb read thread to accumulate data from the
        # weather console.
        time.sleep(_WMR200_USB_POLL_INTERVAL)

    def request_archive_data(self):
        """Request archive packets from console."""
        self._write_cmd(0xDA)

    def print_stats(self):
        """Print summary of driver statistics."""
        log.info('Received packet count live:%d archive:%d control:%d'
                 % (PacketLive.pkt_rx,
                    PacketArchive.pkt_rx,
                    PacketControl.pkt_rx))
        log.info('Received bytes:%d sent bytes:%d'
                 % (self.usb_device.byte_cnt_rd,
                    self.usb_device.byte_cnt_wr))
        log.info('Developer trace stats enabled:%s written:%d dropped:%d errors:%d path:%s'
                 % (self._developer_trace.enabled,
                    self._developer_trace.records_written,
                    self._developer_trace.records_dropped,
                    self._developer_trace.writer_errors,
                    self._developer_trace.path))
        log.info('Driver file log stats enabled:%s written:%d dropped:%d errors:%d path:%s'
                 % (self._driver_file_log.enabled,
                    self._driver_file_log.records_written,
                    self._driver_file_log.records_dropped,
                    self._driver_file_log.writer_errors,
                    self._driver_file_log.path))
        log.info('USB recovery stats successful_reads:%d timeouts:%d '
                 'poll_slice_timeouts:%d timeout_bursts:%d '
                 'max_consecutive_timeouts:%d '
                 'read_pipe_stalls:%d write_transient_errors:%d '
                 'malformed_reports:%d reopens:%d'
                 % (self.usb_device.successful_read_count,
                    self.usb_device.read_timeout_count,
                    self.usb_device.read_poll_timeout_count,
                    self.usb_device.timeout_burst_count,
                    self.usb_device.max_consecutive_read_timeouts,
                    self.usb_device.read_pipe_stall_count,
                    self.usb_device.write_transient_error_count,
                    self.usb_device.malformed_report_count,
                    self.usb_device.reopen_count))
        log.info('Protocol recovery stats stream_resyncs:%d checksum_drops:%d '
                 'stream_gaps:%d'
                 % (self.protocol_resync_count,
                    self.checksum_drop_count,
                    self.usb_device.stream_gap_count))
        log.info('Archive mode stats mode:%s active:%s ready_while_live:%d '
                 'data_while_live:%d dropped_while_live:%d'
                 % (self._protocol_mode, self._archive_recovery_active,
                    self.archive_ready_while_live_count,
                    self.archive_data_while_live_count,
                    self.archive_data_dropped_while_live_count))
        log.info('Packet archive queue len:%d live queue len:%d'
                 % (len(PacketArchive.pkt_queue), len(PacketLive.pkt_queue)))

    def _process_packet_complete(self):
        """Process a completed packet from the wmr200 console.

        Developer diagnostics must never evaluate checksum fields for the
        one-byte control packets, which intentionally have no checksum.
        Malformed short data packets are discarded instead of terminating the
        WeeWX main loop.
        """
        if DEBUG_PACKETS_RAW:
            log.debug(self._pkt.to_string_raw('Packet raw:'))

        trace_packets = (self._developer_trace.enabled and
                         self._developer_trace.include_packets)
        if trace_packets:
            trace_fields = {
                'packet_id': self._pkt.pkt_id,
                'packet_name': self._pkt.pkt_name,
                'command': '0x%02x' % self._pkt.pkt_cmd,
                'actual_size': self._pkt.size_actual(),
                'expected_size': self._pkt.size_expected(),
            }
            if isinstance(self._pkt, PacketControl):
                # Protocol control packets consist of one byte and do not
                # contain a 16-bit checksum.
                trace_fields['checksum_applicable'] = False
            else:
                try:
                    trace_fields['checksum_calculated'] = (
                        '0x%04x' % self._pkt._checksum_calculate())
                    trace_fields['checksum_received'] = (
                        '0x%04x' % self._pkt._checksum_field())
                    trace_fields['checksum_applicable'] = True
                except Exception as exception:
                    # Trace enrichment is best effort only. Verification below
                    # decides whether the packet can be processed.
                    trace_fields['checksum_applicable'] = True
                    trace_fields['checksum_trace_error'] = str(exception)
            self._developer_trace.event(
                'PACKET', 'protocol_packet_complete',
                data=self._pkt._pkt_data, **trace_fields)

        # Checksum errors can be configured as recoverable packet drops.
        try:
            self._pkt.verify_checksum()
        except WMR200PacketParsingError as exception:
            self.checksum_drop_count += 1
            self._developer_trace.event(
                'PACKET', 'protocol_packet_checksum_dropped',
                data=self._pkt._pkt_data,
                packet_id=self._pkt.pkt_id,
                packet_name=self._pkt.pkt_name,
                reason=exception.msg,
                checksum_drop_total=self.checksum_drop_count,
                recovery='packet_dropped_driver_continues')
            log.error(self._pkt.to_string_raw(
                'Discarding packet with invalid checksum: %s ' % exception.msg))
            self._pkt = None
            return
        except weewx.CRCError as exception:
            # A bad checksum invalidates only this packet. It must not restart
            # the USB driver or the WeeWX engine.
            self.checksum_drop_count += 1
            self._developer_trace.event(
                'PACKET', 'protocol_packet_checksum_dropped',
                data=self._pkt._pkt_data,
                packet_id=self._pkt.pkt_id,
                packet_name=self._pkt.pkt_name,
                reason=str(exception),
                checksum_drop_total=self.checksum_drop_count,
                recovery='packet_dropped_driver_continues')
            log.error(self._pkt.to_string_raw(
                'Discarding packet with invalid checksum; driver continues: '
                '%s ' % exception))
            self._pkt = None
            return
        except WMR200ProtocolError as exception:
            # A short or malformed packet is recoverable. Do not let a single
            # bad frame restart the whole WeeWX engine.
            self._developer_trace.event(
                'PACKET', 'protocol_packet_malformed_dropped',
                data=self._pkt._pkt_data,
                packet_id=self._pkt.pkt_id,
                packet_name=self._pkt.pkt_name,
                actual_size=self._pkt.size_actual(),
                expected_size=self._pkt.size_expected(),
                reason=exception.msg)
            log.error(self._pkt.to_string_raw(
                'Discarding malformed packet: %s ' % exception.msg))
            self._pkt = None
            return
        except Exception as exception:
            self._developer_trace.event(
                'PACKET', 'protocol_packet_unhandled_error',
                data=self._pkt._pkt_data,
                packet_id=self._pkt.pkt_id,
                packet_name=self._pkt.pkt_name,
                reason=str(exception))
            raise

        try:
            # Process the actual packet.
            self._pkt.packet_process()
            if trace_packets:
                self._developer_trace.event(
                    'PACKET', 'protocol_packet_decoded',
                    packet_id=self._pkt.pkt_id,
                    packet_name=self._pkt.pkt_name,
                    record=self._pkt.packet_record())
            if self._pkt.packet_live_data():
                PacketLive.pkt_queue.append(self._pkt)
                log.debug('  Queuing live packet rx:%d live_queue_len:%d' %
                          (PacketLive.pkt_rx, len(PacketLive.pkt_queue)))
            elif self._pkt.packet_archive_data():
                if self._archive_recovery_active:
                    PacketArchive.pkt_queue.append(self._pkt)
                    log.debug(
                        '  Queuing archive packet rx:%d archive_queue_len:%d'
                        % (PacketArchive.pkt_rx, len(PacketArchive.pkt_queue)))
                else:
                    self.archive_data_dropped_while_live_count += 1
                    record = self._pkt.packet_record()
                    self._developer_trace.event(
                        'ARCHIVE', 'archive_record_dropped_while_live',
                        packet_id=self._pkt.pkt_id,
                        record_ts=record.get('dateTime'),
                        protocol_mode=self._protocol_mode,
                        dropped_total=self.archive_data_dropped_while_live_count,
                        action='drop_from_runtime_queue_reassert_live')
                    log.warning(
                        'Dropping archive D2 received outside startup recovery; '
                        'packet_id=%d' % self._pkt.pkt_id)
                    self.ready_to_poke(
                        True, reason='archive_record_dropped_while_live')
            else:
                log.debug(('  Acknowledged control packet rx:%d') % PacketControl.pkt_rx)
        except WMR200PacketParsingError as e:
            # Drop any bogus packets.
            log.error(self._pkt.to_string_raw('Discarding bogus packet: %s ' % e.msg))

        # Reset this packet to get ready for next one
        self._pkt = None

    def genLoopPackets(self):
        """Main generator function that continuously returns loop packets

        weewx api to return live records."""
        # Reset the current packet upon entry and make LIVE mode explicit.
        self._pkt = None
        self._archive_recovery_active = False
        self._set_protocol_mode('live', reason='genLoopPackets_entry')
        self._drop_pending_archive_packets('genLoopPackets_entry')
        self.ready_to_poke(True, reason='enter_live_mode')

        log.debug('genLoop() phase getting live packets')

        while True:
            # Loop through indefinitely generating records to the
            # weewx engine.  This loop may resume at the yield()
            # or upon entry during any exception, even an exception
            # not generated from this driver.  e.g. weewx.service.
            if self._pkt is not None and self._pkt.packet_complete():
                self._process_packet_complete()

            # If it's time to poke the console and we are not
            # in the middle of collecting a packet then do it here.
            if self.is_ready_to_poke() and self._pkt is None:
                self._poke_console()

            # Pull data from the weather console.
            # This may create a packet or append data to existing packet.
            self._poll_for_data()

            # Yield any live packets we may have obtained from this callback
            # or queued from other driver callback services.
            while PacketLive.pkt_queue:
                pkt = PacketLive.pkt_queue.pop(0)
                if DEBUG_PACKETS_COOKED:
                    pkt.print_cooked()
                log.debug('genLoop() Yielding live queued packet id:%d' % pkt.pkt_id)
                mapped = self._sensors_to_fields(pkt.packet_record(),
                                                 self._sensor_map)
                if mapped:
                    yield mapped

    def XXXgenArchiveRecords(self, since_ts=0):
        """A generator function to return archive packets from the wmr200.
        
        weewx api to return archive records.
        since_ts: A timestamp in database time. All data since but not 
        including this time will be returned.
        Pass in None for all data
       
        NOTE: This API is disabled so that the weewx engine will default
        to using sofware archive generation.  There may be a way
        to use hardware generation if one plays with not poking the console
        which would allow archive packets to be created.

        yields: a sequence of dictionary records containing the console 
        data."""
        log.debug('genArchive() phase getting archive packets since %s'
                  % weeutil.weeutil.timestamp_to_string(since_ts))

        if self.use_pc_time and self.time_drift is None:
            log.info(('genArchive() Unable to process archive packets until live packet received'))
            return

        while True:
            # Loop through indefinitely generating records to the
            # weewx engine.  This loop may resume at the yield()
            # or upon entry during any exception, even an exception
            # not generated from this driver.  e.g. weewx.service.
            if self._pkt is not None and self._pkt.packet_complete():
                self._process_packet_complete()

            # If it's time to poke the console and we are not
            # in the middle of collecting a packet then do it here.
            if self.is_ready_to_poke() and self._pkt is None:
                self._poke_console()

            # Pull data from the weather console.
            # This may create a packet or append data to existing packet.
            self._poll_for_data()

            # Yield any live packets we may have obtained from this callback
            # or queued from other driver callback services.
            while PacketArchive.pkt_queue:
                pkt = PacketArchive.pkt_queue.pop(0)
                # If we are using PC time we need to adjust the record timestamp
                # with the PC drift.
                if self.use_pc_time:
                    pkt.timestamp_adjust_drift()

                if DEBUG_PACKETS_COOKED:
                    pkt.print_cooked()
                if pkt.timestamp_record() > since_ts:
                    log.debug('genArchive() Yielding received archive record after requested timestamp')
                    mapped = self._sensors_to_fields(pkt.packet_record(),
                                                     self._sensor_map)
                    yield mapped
                else:
                    log.info('genArchive() Ignoring received archive record before requested timestamp')

    def genStartupRecords(self, since_ts=0):
        """Present console archive packets on driver startup.

        gp8/gp9 add diagnostic accounting around the existing recovery algorithm.
        It does not clear the console archive. Every received archive record is
        classified in the developer trace as yielded, old, duplicate/out of
        order, threshold-rejected, or sub-minute. A sub-minute anomaly is now
        dropped locally instead of terminating the entire startup recovery.
        """
        log.debug('genStartup() phase getting archive packets since %s'
                  % weeutil.weeutil.timestamp_to_string(since_ts))

        # Reset the current packet upon entry.
        self._pkt = None

        # Time after last archive packet to indicate there are likely no more
        # archive packets left to drain.
        timestamp_last_archive_rx = int(time.time() + 0.5)

        # Statistics used by the original code and by gp8 diagnostics.
        timestamp_packet_first = None
        timestamp_packet_current = None
        timestamp_packet_previous = None
        cnt = 0

        if since_ts is None:
            log.info('genStartup() Database initialization')
            since_ts = 0
        since_ts = int(since_ts)

        recovery_started_monotonic = time.monotonic()
        recovery_outcome = 'consumer_closed'
        archive_received = 0
        archive_before_since = 0
        archive_duplicate = 0
        archive_out_of_order = 0
        archive_threshold_drop = 0
        archive_subminute_drop = 0
        archive_gap_count = 0
        archive_gap_seconds = 0
        archive_max_gap_seconds = 0
        first_yielded_ts = None
        last_yielded_ts = None
        drift_wait_reported = False

        def _utc_iso(epoch_value):
            if epoch_value is None:
                return None
            try:
                return datetime.datetime.fromtimestamp(
                    float(epoch_value), datetime.timezone.utc).isoformat(
                        timespec='seconds')
            except (TypeError, ValueError, OverflowError, OSError):
                return None

        self._archive_recovery_active = True
        self._set_protocol_mode(
            'archive_recovery', reason='genStartupRecords_entry')

        self._developer_trace.event(
            'ARCHIVE', 'archive_recovery_start',
            since_ts=since_ts,
            since_utc=_utc_iso(since_ts),
            archive_interval=self._archive_interval,
            archive_startup=self._archive_startup,
            archive_threshold=self._archive_threshold,
            use_pc_time=self.use_pc_time,
            time_drift=self.time_drift,
            erase_archive=self._erase_archive,
            action='drain_console_archive_without_erasing')

        try:
            while True:
                if self._pkt is not None and self._pkt.packet_complete():
                    self._process_packet_complete()

                if self.is_ready_to_poke() and self._pkt is None:
                    self._poke_console()

                self._poll_for_data()

                while PacketArchive.pkt_queue:
                    timestamp_last_archive_rx = int(time.time() + 0.5)

                    # PC-time mode needs one live timestamp to calculate the
                    # console/host drift before archive timestamps are adjusted.
                    if self.use_pc_time and self.time_drift is None:
                        if not drift_wait_reported:
                            self._developer_trace.event(
                                'ARCHIVE', 'archive_recovery_waiting_for_time_drift',
                                queued_records=len(PacketArchive.pkt_queue),
                                action='retain_archive_queue_until_live_timestamp')
                            drift_wait_reported = True
                        log.info('genStartup() Delaying archive packet processing until live packet received')
                        break

                    if drift_wait_reported:
                        self._developer_trace.event(
                            'ARCHIVE', 'archive_recovery_time_drift_ready',
                            time_drift=self.time_drift,
                            queued_records=len(PacketArchive.pkt_queue))
                        drift_wait_reported = False

                    pkt = PacketArchive.pkt_queue.pop(0)
                    archive_received += 1

                    if self.use_pc_time:
                        pkt.timestamp_adjust_drift()

                    current_ts = int(pkt.timestamp_record())
                    timestamp_packet_current = current_ts
                    if timestamp_packet_first is None:
                        timestamp_packet_first = current_ts

                    if timestamp_packet_previous is None:
                        timestamp_packet_previous = (
                            current_ts if since_ts == 0 else since_ts)

                    previous_ts = int(timestamp_packet_previous)
                    timestamp_packet_interval = current_ts - previous_ts
                    disposition = None
                    gap_seconds = 0

                    if timestamp_packet_interval < 1:
                        if timestamp_packet_interval == 0:
                            archive_duplicate += 1
                            disposition = 'duplicate'
                        else:
                            archive_out_of_order += 1
                            disposition = 'out_of_order'
                        log.info(
                            'genStartup() Discarding archive record %s; '
                            'current timestamp:%s; previous timestamp:%s' %
                            (disposition,
                             weeutil.weeutil.timestamp_to_string(current_ts),
                             weeutil.weeutil.timestamp_to_string(previous_ts)))

                    elif current_ts > (previous_ts + self._archive_threshold):
                        archive_threshold_drop += 1
                        disposition = 'threshold_exceeded'
                        log.info(
                            'genStartup() Discarding received archive record '
                            'exceeding archive threshold cnt:%d threshold:%d '
                            'timestamp:%s' %
                            (cnt, self._archive_threshold,
                             weeutil.weeutil.timestamp_to_string(current_ts)))

                    elif current_ts > since_ts:
                        packet_record_interval = int(
                            timestamp_packet_interval / 60.0)
                        if packet_record_interval == 0:
                            archive_subminute_drop += 1
                            disposition = 'subminute_interval'
                            log.warning(
                                'genStartup() Discarding sub-minute archive '
                                'record but CONTINUING recovery; interval=%d '
                                'timestamp=%s' %
                                (timestamp_packet_interval,
                                 weeutil.weeutil.timestamp_to_string(current_ts)))
                        else:
                            # Only an accepted archive record advances the
                            # sequencing reference. A malformed sub-minute
                            # timestamp therefore cannot poison the rest of
                            # the startup recovery.
                            timestamp_packet_previous = current_ts
                            if timestamp_packet_interval > self._archive_interval:
                                gap_seconds = max(
                                    0,
                                    timestamp_packet_interval -
                                    self._archive_interval)
                                archive_gap_count += 1
                                archive_gap_seconds += gap_seconds
                                archive_max_gap_seconds = max(
                                    archive_max_gap_seconds, gap_seconds)
                                self._developer_trace.event(
                                    'ARCHIVE', 'archive_recovery_gap',
                                    previous_ts=previous_ts,
                                    previous_utc=_utc_iso(previous_ts),
                                    current_ts=current_ts,
                                    current_utc=_utc_iso(current_ts),
                                    interval_seconds=timestamp_packet_interval,
                                    expected_interval_seconds=self._archive_interval,
                                    missing_span_seconds=gap_seconds,
                                    gap_count=archive_gap_count,
                                    classification='archive_time_gap_detected')

                            pkt.record_update({
                                'interval': packet_record_interval})
                            pkt.record_update(
                                adjust_rain(pkt, PacketArchiveData))
                            cnt += 1
                            disposition = 'yielded'
                            if first_yielded_ts is None:
                                first_yielded_ts = current_ts
                            last_yielded_ts = current_ts

                            log.debug(
                                'genStartup() Yielding archive record cnt:%d '
                                'after requested timestamp:%d pkt_interval:%d '
                                'pkt:%s' %
                                (cnt, since_ts, timestamp_packet_interval,
                                 weeutil.weeutil.timestamp_to_string(current_ts)))
                            if DEBUG_PACKETS_COOKED:
                                pkt.print_cooked()
                            mapped = self._sensors_to_fields(
                                pkt.packet_record(), self._sensor_map)

                    else:
                        timestamp_packet_previous = current_ts
                        archive_before_since += 1
                        disposition = 'before_since_ts'
                        log.info(
                            'genStartup() Discarding received archive record '
                            'before time requested cnt:%d timestamp:%s' %
                            (cnt,
                             weeutil.weeutil.timestamp_to_string(since_ts)))

                    self._developer_trace.event(
                        'ARCHIVE', 'archive_record_evaluated',
                        packet_id=getattr(pkt, 'pkt_id', None),
                        record_number=archive_received,
                        record_ts=current_ts,
                        record_utc=_utc_iso(current_ts),
                        previous_ts=previous_ts,
                        previous_utc=_utc_iso(previous_ts),
                        interval_seconds=timestamp_packet_interval,
                        disposition=disposition,
                        gap_seconds=gap_seconds,
                        yielded_total=cnt,
                        before_since_total=archive_before_since,
                        duplicate_total=archive_duplicate,
                        out_of_order_total=archive_out_of_order,
                        threshold_drop_total=archive_threshold_drop,
                        subminute_drop_total=archive_subminute_drop)

                    if disposition == 'yielded':
                        yield mapped

                if (int(time.time() + 0.5) - timestamp_last_archive_rx >
                        self._archive_startup):
                    recovery_outcome = 'archive_drained'
                    log.info(
                        'genStartup() phase exiting since looks like all '
                        'archive packets have been retrieved after %d sec '
                        'cnt:%d' % (self._archive_startup, cnt))
                    if timestamp_packet_first is not None:
                        data_span = (timestamp_packet_current -
                                     timestamp_packet_first)
                        log.info(
                            'genStartup() Yielded %d packets spanning %d sec '
                            'between these dates %s ==> %s' %
                            (cnt, data_span,
                             weeutil.weeutil.timestamp_to_string(
                                 timestamp_packet_first),
                             weeutil.weeutil.timestamp_to_string(
                                 timestamp_packet_current)))
                        if data_span > 0:
                            log.info(
                                'genStartup() Average yielded packets per '
                                'data-minute:%f' %
                                (cnt / (data_span / 60.0)))
                    return
        except Exception as exception:
            recovery_outcome = 'error'
            self._developer_trace.event(
                'ARCHIVE', 'archive_recovery_error',
                error_type=type(exception).__name__,
                reason=str(exception),
                records_received=archive_received,
                records_yielded=cnt)
            raise
        finally:
            elapsed_wall_s = max(
                0.0, time.monotonic() - recovery_started_monotonic)
            data_span_s = None
            if (timestamp_packet_first is not None and
                    timestamp_packet_current is not None):
                data_span_s = (timestamp_packet_current -
                               timestamp_packet_first)
            self._developer_trace.event(
                'ARCHIVE', 'archive_recovery_complete',
                outcome=recovery_outcome,
                since_ts=since_ts,
                since_utc=_utc_iso(since_ts),
                elapsed_wall_s=round(elapsed_wall_s, 3),
                records_received=archive_received,
                records_yielded=cnt,
                records_before_since=archive_before_since,
                records_duplicate=archive_duplicate,
                records_out_of_order=archive_out_of_order,
                records_threshold_dropped=archive_threshold_drop,
                records_subminute_dropped=archive_subminute_drop,
                gaps_detected=archive_gap_count,
                gap_seconds_total=archive_gap_seconds,
                max_gap_seconds=archive_max_gap_seconds,
                first_received_ts=timestamp_packet_first,
                first_received_utc=_utc_iso(timestamp_packet_first),
                last_received_ts=timestamp_packet_current,
                last_received_utc=_utc_iso(timestamp_packet_current),
                first_yielded_ts=first_yielded_ts,
                first_yielded_utc=_utc_iso(first_yielded_ts),
                last_yielded_ts=last_yielded_ts,
                last_yielded_utc=_utc_iso(last_yielded_ts),
                data_span_seconds=data_span_s,
                pending_archive_queue=len(PacketArchive.pkt_queue))
            self._archive_recovery_active = False
            self._set_protocol_mode(
                'live_pending', reason='genStartupRecords_exit')
            self.ready_to_poke(
                True, reason='archive_recovery_complete')

    def closePort(self):
        """Closes the USB port to the device.
        
        weewx api to shutdown the weather console."""
        # Send a best-effort command indicating that the driver is leaving.
        # Cleanup must continue even if the USB device has already vanished.
        try:
            self._write_cmd(0xDF)
        except weewx.WeeWxIOError as exception:
            log.warning('Unable to send shutdown command to WMR200: %s' %
                        exception)
        # Let the polling thread die off.
        self._poll_device_enable = False
        # Join with the polling thread.
        self._thread_usb_poll.join()
        if self._thread_usb_poll.is_alive():
            log.error('USB polling thread still alive')
        else:
            log.info('USB polling thread expired')

        # Shutdown the watchdog thread.
        self.sock_wr.sendall(b'shutdown')
        # Join with the watchdog thread.
        self._thread_watchdog.join()
        if self._thread_watchdog.is_alive():
            log.error('Watchdog thread still alive')
        else:
            log.info('Watchdog thread expired')
        self.sock_wr.close()
        self.sock_rd.close()

        self.print_stats()
        # Indicate if queues have not been drained.
        if len(PacketArchive.pkt_queue):
            log.warning('Exiting with packets still in archive queue cnt:%d' %
                        len(PacketArchive.pkt_queue))
        if len(PacketLive.pkt_queue):
            log.warning('Exiting with packets still in live queue cnt:%d' %
                        len(PacketLive.pkt_queue))

        # Shutdown the USB acccess to the weather console device.
        self.usb_device.close_device()
        self._developer_trace.event(
            'EVENT', 'driver_stop',
            received_bytes=self.usb_device.byte_cnt_rd,
            sent_bytes=self.usb_device.byte_cnt_wr,
            successful_reads=self.usb_device.successful_read_count,
            read_timeouts=self.usb_device.read_timeout_count,
            poll_slice_timeouts=self.usb_device.read_poll_timeout_count,
            timeout_bursts=self.usb_device.timeout_burst_count,
            max_consecutive_timeouts=self.usb_device.max_consecutive_read_timeouts,
            read_pipe_stalls=self.usb_device.read_pipe_stall_count,
            write_transient_errors=self.usb_device.write_transient_error_count,
            malformed_reports=self.usb_device.malformed_report_count,
            stream_gaps=self.usb_device.stream_gap_count,
            protocol_resyncs=self.protocol_resync_count,
            checksum_drops=self.checksum_drop_count,
            reopens=self.usb_device.reopen_count,
            protocol_mode=self._protocol_mode,
            archive_ready_while_live=self.archive_ready_while_live_count,
            archive_data_while_live=self.archive_data_while_live_count,
            archive_data_dropped_while_live=(
                self.archive_data_dropped_while_live_count))
        self._developer_trace.stop()
        log.info('Driver gracefully exiting')
        self._driver_file_log.stop()

    @staticmethod
    def _sensors_to_fields(oldrec, sensor_map):
        # map a record with observation names to a record with db field names
        newrec = None
        if oldrec:
            newrec = dict()
            for k in sensor_map:
                if sensor_map[k] in oldrec:
                    newrec[k] = oldrec[sensor_map[k]]
            if newrec:
                newrec['dateTime'] = oldrec['dateTime']
                newrec['usUnits'] = oldrec['usUnits']
                if 'interval' in oldrec:
                    newrec['interval'] = oldrec['interval']
        if DEBUG_MAPPING:
            log.debug("sensors: %s" % oldrec)
            log.debug("fields: %s" % newrec)
        return newrec


class WMR200ConfEditor(weewx.drivers.AbstractConfEditor):
    @property
    def default_stanza(self):
        return """
[WMR200]
    model = WMR200
    driver = user.wmr200

    use_pc_time = True
    erase_archive = False
    archive_interval = 60
    archive_startup = 120
    archive_threshold = 1512000
    ignore_checksum = False
    sensor_status = True

    # USB recovery inherited from gp7-streamresync. gp9 uses short
    # interrupt-read slices so the shared PyUSB lock cannot delay D0 for the
    # old 15-second blocking-read window. Logical timeout health remains 15 s.
    usb_write_retries = 3
    usb_read_retries = 2
    usb_retry_delay = 0.5
    usb_reopen_on_failure = True
    usb_read_slice_timeout = 2.0
    usb_logical_timeout_seconds = 15
    usb_timeout_warn_consecutive = 2
    usb_timeout_error_consecutive = 4
    usb_health_interval = 300

    # Structured USB/protocol/archive trace. One active file + four backups.
    developer_trace = true
    developer_trace_path = /var/log/weewx/wmr200-developer-trace.jsonl
    developer_trace_max_mb = 10
    developer_trace_backups = 4
    developer_trace_queue_size = 4096
    developer_trace_include_timeouts = true
    developer_trace_include_packets = true

    # Complete asynchronous text log for this driver only.
    driver_file_log = true
    driver_file_log_path = /var/log/weewx/wmr200-debug.log
    driver_file_log_level = DEBUG
    driver_file_log_max_mb = 10
    driver_file_log_backups = 4

    [[sensor_map]]
"""

    def modify_config(self, config_dict):
        print("""
Setting rainRate and windchill calculations to hardware.""")
        config_dict.setdefault('StdWXCalculate', {})
        config_dict['StdWXCalculate'].setdefault('Calculations', {})
        config_dict['StdWXCalculate']['Calculations']['rainRate'] = 'hardware'
        config_dict['StdWXCalculate']['Calculations']['windchill'] = 'hardware'
        config_dict['StdWXCalculate']['Calculations']['heatindex'] = 'hardware'
