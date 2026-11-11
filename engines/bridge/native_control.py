"""Cancel Alice's dialogue or pause its player using the station's local IPC.

Wire mapping is the stock CLI cancel action, also documented by spotiya:
https://github.com/klockky/spotiya/blob/main/src/voice.rs
"""
import base64
from uuid import uuid4
from bridge import ssh, socat_command


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def byte_field(field, value):
    return varint(field * 8 + 2) + varint(len(value)) + value


def fields(data):
    """Read just the small protobuf envelope used by local Alice events."""
    offset = 0

    def number():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise ValueError('Truncated native event')
            byte = data[offset]
            offset += 1
            if shift == 63 and byte > 1:
                raise ValueError('Invalid native integer')
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError('Invalid native integer')

    result = {}
    while offset < len(data):
        key = number()
        if key >> 3 == 0:
            raise ValueError('Invalid native field')
        wire = key & 7
        if wire == 0:
            value = number()
        elif wire in [1, 2, 5]:
            length = number() if wire == 2 else (8 if wire == 1 else 4)
            if offset + length > len(data):
                raise ValueError('Truncated native field')
            value = data[offset:offset + length] if wire == 2 else None
            offset += length
        else:
            raise ValueError('Unsupported native field')
        result[key >> 3] = value
    return result


def recognition_event(data):
    """Return final native recognition only; callers retain command labels only."""
    try:
        envelope = fields(data)
        if envelope.get(253) != 3:
            return None
        state = fields(envelope[252])
        if state.get(1) != 3:  # BUSY: final query before the native reply.
            return None
        result = fields(state[15])
        if result.get(2) != 1 or result.get(4) != 1:
            return None
        text, request = result[1], state[7]
        if (text != state.get(2) or not 0 < len(text) <= 1024 or
                len(request) != 36):
            return None
        return request.decode('ascii'), text.decode('utf-8')
    except (ValueError, TypeError, KeyError, UnicodeError):
        return None


def rpc_frame(capability, field, payload):
    method = byte_field(7, capability) + byte_field(field, payload)
    message = byte_field(252, method) + varint(253 * 8) + varint(210)
    return len(message).to_bytes(4, 'little') + message


def cancel_frame():
    # Stock CLI 'c': cancel the dialogue, not its separate music player.
    return rpc_frame(b'AliceCapability', 8, b'\x08\x03')


def pause_frame():
    # Stock CLI 'o': PlaybackControlCapabilityMethod kind 1 (pause).
    # The source describes the external audio owner, as in spotiya.
    source = (varint(1 * 8) + varint(14) + varint(2 * 8) + varint(3) +
              varint(3 * 8) + varint(1) + byte_field(4, str(uuid4()).encode()) +
              byte_field(6, b'ChatGPTVoice'))
    method = b'\x08\x01' + byte_field(5, source) + varint(7 * 8) + varint(0)
    return rpc_frame(b'PlaybackControlCapability', 13, method)


def send_frame(host, frame):
    encoded = base64.b64encode(frame).decode()
    ssh(host, f"printf '%s' '{encoded}' | base64 -d | "
        f'{socat_command(host)} -u STDIN TCP:127.0.0.1:9875,connect-timeout=2', timeout=6)


def cancel_alice(host):
    send_frame(host, cancel_frame())
