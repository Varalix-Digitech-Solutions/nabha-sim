from __future__ import annotations

import json
import ssl
import threading
import time
import uuid
from collections import deque
from copy import deepcopy
from pathlib import Path

from paho.mqtt import client as mqtt_client

from .generators import DeviceRuntime, catalog, default_point, utc_iso

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

PROTO = {
    "3.1": mqtt_client.MQTTv31,
    "3.1.1": mqtt_client.MQTTv311,
    "5.0": mqtt_client.MQTTv5,
}


NABHA_HOST = "mqtt.nabha.cloud"
NABHA_PORT = 8883


def default_connection(index: int) -> dict:
    return {
        "index": index, "enabled": False, "name": "", "client_id": "",
        "host": NABHA_HOST, "port": NABHA_PORT, "username": "", "password": "",


        "tls": True, "timeout": 10, "keepalive": 60, "version": "3.1.1",
        "lwt_topic": "", "lwt_qos": 0, "lwt_retain": False, "lwt_payload": "",


        "birth_payload": "online",
    }


def default_device() -> dict:
    return {"id": "", "name": "device-1", "kind": "direct", "parent": "",
            "address": "", "preset": "", "points": []}


def default_binding() -> dict:
    return {
        "id": "", "connection": 1, "enabled": True,
        "device": "", "include_children": False,
        "data_format": "flat", "template": "",
        "topic": "", "qos": 1, "retain": False,
        "mode": "periodic", "interval": 10, "pub_type": "processed",
        "cov_deadband": 0.5, "cov_min_interval": 2, "cov_heartbeat": 300,
    }


def default_sub() -> dict:
    return {"id": "", "connection": 1, "enabled": True, "topic": "", "qos": 0,
            "sub_type": "command"}


class Connection:

    def __init__(self, cfg: dict, engine: "Engine"):
        self.cfg = cfg
        self.engine = engine
        self.client: mqtt_client.Client | None = None
        self.status = "Disconnected"
        self.started = False
        self.stats = {"pub_count": 0, "sub_count": 0, "last_pub": None, "connected_at": None}

    def start(self):
        cfg = self.cfg
        if not cfg["host"]:
            self.status = "Error: no host"
            return False
        self.stop(quiet=True)
        proto = PROTO.get(cfg["version"], mqtt_client.MQTTv311)
        cid = cfg["client_id"] or f"nabha-sim-{cfg['index']}-{uuid.uuid4().hex[:6]}"
        kwargs = dict(client_id=cid, protocol=proto,
                      callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
        if proto != mqtt_client.MQTTv5:
            kwargs["clean_session"] = True
        c = mqtt_client.Client(**kwargs)
        if cfg["username"]:
            c.username_pw_set(cfg["username"], cfg["password"] or None)
        if cfg["tls"]:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            c.tls_set_context(ctx)
        if cfg["lwt_topic"]:
            c.will_set(cfg["lwt_topic"], cfg["lwt_payload"] or "",
                       qos=int(cfg["lwt_qos"]), retain=bool(cfg["lwt_retain"]))
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client = c
        self.status = "Connecting"
        self.started = True
        try:
            c.connect_async(cfg["host"], int(cfg["port"]), keepalive=int(cfg["keepalive"]))
            c.loop_start()
            self.engine.log("conn", f"[conn {cfg['index']}] connecting to "
                                    f"{cfg['host']}:{cfg['port']} as {cid} (v{cfg['version']})")
            return True
        except Exception as e:
            self.status = f"Error: {e}"
            self.engine.log("error", f"[conn {cfg['index']}] connect failed: {e}")
            return False

    def stop(self, quiet=False):
        self.started = False
        if self.client:
            try:


                if self.status == "Connected" and self.cfg.get("lwt_topic"):
                    self.client.publish(self.cfg["lwt_topic"],
                                        self.cfg.get("lwt_payload") or "offline",
                                        qos=int(self.cfg.get("lwt_qos", 0)))
                    time.sleep(0.2)
            except Exception:
                pass
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
            self.client = None
        if self.status != "Disconnected":
            self.status = "Disconnected"
            if not quiet:
                self.engine.log("conn", f"[conn {self.cfg['index']}] stopped")

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False) or (isinstance(reason_code, int) and reason_code != 0):
            self.status = f"Error: {reason_code}"
            self.engine.log("error", f"[conn {self.cfg['index']}] connack error: {reason_code}")
            return
        self.status = "Connected"
        self.stats["connected_at"] = utc_iso()
        self.engine.log("conn", f"[conn {self.cfg['index']}] CONNECTED to "
                                f"{self.cfg['host']}:{self.cfg['port']}")

        if self.cfg.get("lwt_topic") and self.cfg.get("birth_payload"):
            try:
                client.publish(self.cfg["lwt_topic"], self.cfg["birth_payload"],
                               qos=int(self.cfg.get("lwt_qos", 0)))
                self.engine.log("conn", f"[conn {self.cfg['index']}] birth published "
                                        f"to {self.cfg['lwt_topic']}")
            except Exception as e:
                self.engine.log("error", f"[conn {self.cfg['index']}] birth publish failed: {e}")
        self.engine.resubscribe(self.cfg["index"])

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        if self.started:
            self.status = "Connecting"
            self.engine.log("conn", f"[conn {self.cfg['index']}] lost connection "
                                    f"({reason_code}), reconnecting…")
        else:
            self.status = "Disconnected"

    def _on_message(self, client, userdata, msg):
        self.stats["sub_count"] += 1
        try:
            payload = msg.payload.decode("utf-8", "replace")
        except Exception:
            payload = repr(msg.payload)
        self.engine.on_sub_message(self.cfg["index"], msg.topic, payload, msg.qos)

    def publish(self, topic, payload, qos, retain) -> bool:
        if not self.client or self.status != "Connected":
            return False
        r = self.client.publish(topic, payload, qos=int(qos), retain=bool(retain))
        ok = r.rc == mqtt_client.MQTT_ERR_SUCCESS
        if ok:
            self.stats["pub_count"] += 1
            self.stats["last_pub"] = utc_iso()
        return ok


class Engine:
    MAX_LOG = 400
    MAX_MSGS = 200

    def __init__(self):
        self.lock = threading.RLock()
        self.connections: dict[int, Connection] = {}
        self.devices: list[dict] = []
        self.runtimes: dict[str, DeviceRuntime] = {}
        self.bindings: list[dict] = []
        self.subs: list[dict] = []
        self._bind_state: dict[str, dict] = {}
        self.logs = deque(maxlen=self.MAX_LOG)
        self.sub_messages = deque(maxlen=self.MAX_MSGS)
        self.log_seq = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.load()


    def load(self):
        cfg = {}
        if CONFIG_PATH.exists():
            try:
                cfg = json.loads(CONFIG_PATH.read_text())
            except Exception as e:
                backup = CONFIG_PATH.with_suffix(f".corrupt-{int(time.time())}.json")
                try:
                    backup.write_bytes(CONFIG_PATH.read_bytes())
                except Exception:
                    pass
                self.log("error", f"config.json unreadable ({e}); backed up to {backup.name}, starting fresh")
        conns = {c["index"]: c for c in cfg.get("connections", [])}
        for i in range(1, 5):
            c = default_connection(i)
            c.update(conns.get(i, {}))
            c["host"], c["port"] = NABHA_HOST, NABHA_PORT
            self.connections[i] = Connection(c, self)
        self.devices = [dict(default_device(), **d) for d in cfg.get("devices", [])]
        for d in self.devices:
            d["points"] = [dict(default_point(), **p) for p in d.get("points", [])]
            self.runtimes[d["id"]] = DeviceRuntime(d)
        self.bindings = [dict(default_binding(), **b) for b in cfg.get("bindings", [])]
        for b in self.bindings:
            self._bind_state[b["id"]] = {"last_pub": 0.0, "last_values": {}}
        self.subs = [dict(default_sub(), **s) for s in cfg.get("subs", [])]

    def save(self):
        with self.lock:
            data = json.dumps({
                "connections": [self.connections[i].cfg for i in sorted(self.connections)],
                "devices": self.devices,
                "bindings": self.bindings,
                "subs": self.subs,
            }, indent=2)
            tmp = CONFIG_PATH.with_suffix(".tmp")
            tmp.write_text(data)
            tmp.replace(CONFIG_PATH)


    def log(self, kind, text):
        self.log_seq += 1
        self.logs.append({"seq": self.log_seq, "ts": utc_iso(), "kind": kind, "text": text})

    def on_sub_message(self, conn_index, topic, payload, qos):
        self.sub_messages.append({"ts": utc_iso(), "connection": conn_index,
                                  "topic": topic, "payload": payload, "qos": qos})
        self.log("sub", f"[conn {conn_index}] ← {topic}  {payload[:160]}")


    def set_connection(self, index: int, fields: dict):
        with self.lock:
            conn = self.connections[index]
            was_started = conn.started
            allowed = default_connection(index).keys()
            for k, v in fields.items():
                if k in allowed and k not in ("index", "host", "port"):
                    conn.cfg[k] = v
            conn.cfg["host"], conn.cfg["port"] = NABHA_HOST, NABHA_PORT
            self.save()
            if was_started and conn.cfg["enabled"]:
                conn.start()
            elif not conn.cfg["enabled"]:
                conn.stop()
            return conn.cfg

    def start_connection(self, index: int):
        with self.lock:
            c = self.connections[index]
            c.cfg["enabled"] = True
            self.save()
            return c.start()

    def stop_connection(self, index: int):
        with self.lock:
            self.connections[index].stop()

    def resubscribe(self, conn_index: int):
        c = self.connections[conn_index]
        if not c.client:
            return
        for s in self.subs:
            if s["enabled"] and s["connection"] == conn_index and s["topic"]:
                c.client.subscribe(s["topic"], qos=int(s["qos"]))
                self.log("sub", f"[conn {conn_index}] subscribed {s['topic']} (q{s['qos']})")


    def upsert_device(self, fields: dict) -> dict:
        with self.lock:
            did = fields.get("id") or uuid.uuid4().hex[:8]
            existing = next((d for d in self.devices if d["id"] == did), None)
            merged = dict(existing or default_device())
            for k in default_device().keys():
                if k in fields:
                    merged[k] = fields[k]
            merged["id"] = did
            merged["points"] = [dict(default_point(), **p) for p in merged.get("points", [])]

            if merged["parent"]:
                par = next((d for d in self.devices if d["id"] == merged["parent"]), None)
                if not par or par["kind"] != "gateway" or merged["parent"] == did:
                    merged["parent"] = ""
            if existing:
                self.devices[self.devices.index(existing)] = merged
                self.runtimes[did].rebuild(merged)
            else:
                self.devices.append(merged)
                self.runtimes[did] = DeviceRuntime(merged)
            self.save()
            self.log("sim", f"device saved: {merged['name']} ({merged['kind']}"
                            f"{', sub of ' + self.device_name(merged['parent']) if merged['parent'] else ''})"
                            f" · {len(merged['points'])} points")
            return merged

    def delete_device(self, did: str):
        with self.lock:
            dev = next((d for d in self.devices if d["id"] == did), None)
            if not dev:
                return

            for d in self.devices:
                if d["parent"] == did:
                    d["parent"] = ""
            removed = [b for b in self.bindings if b["device"] == did]
            self.bindings = [b for b in self.bindings if b["device"] != did]
            for b in removed:
                self._bind_state.pop(b["id"], None)
            self.devices.remove(dev)
            self.runtimes.pop(did, None)
            self.save()
            self.log("sim", f"device deleted: {dev['name']}"
                            f"{f' (+{len(removed)} bindings)' if removed else ''}")

    def device_name(self, did: str) -> str:
        d = next((x for x in self.devices if x["id"] == did), None)
        return d["name"] if d else "?"

    def set_point(self, did: str, point: str, value):
        rt = self.runtimes.get(did)
        if not rt:
            return False
        ok = rt.set_value(point, value)
        if ok:
            self.log("sim", f"[{self.device_name(did)}] point {point} set to {value}")
        return ok

    def children_of(self, did: str) -> list[DeviceRuntime]:
        return [self.runtimes[d["id"]] for d in self.devices
                if d["parent"] == did and d["id"] in self.runtimes]


    MULTIPLEX_FORMATS = frozenset({"enocean_gw", "array", "zigbee2mqtt"})

    @classmethod
    def _wants_children(cls, b: dict) -> bool:
        return bool(b.get("include_children")) or b.get("data_format") in cls.MULTIPLEX_FORMATS


    def upsert_binding(self, fields: dict) -> dict:
        with self.lock:
            bid = fields.get("id") or uuid.uuid4().hex[:8]
            existing = next((b for b in self.bindings if b["id"] == bid), None)
            merged = dict(existing or default_binding())
            for k in default_binding().keys():
                if k in fields:
                    merged[k] = fields[k]
            merged["id"] = bid
            if existing:
                self.bindings[self.bindings.index(existing)] = merged
            else:
                self.bindings.append(merged)
            self._bind_state.setdefault(bid, {"last_pub": 0.0, "last_values": {}})
            self.save()
            return merged

    def delete_binding(self, bid: str):
        with self.lock:
            self.bindings = [b for b in self.bindings if b["id"] != bid]
            self._bind_state.pop(bid, None)
            self.save()

    def publish_now(self, bid: str) -> bool:
        with self.lock:
            b = next((x for x in self.bindings if x["id"] == bid), None)
            return self._do_publish(b, force=True) if b else False


    def upsert_sub(self, fields: dict) -> dict:
        with self.lock:
            sid = fields.get("id") or uuid.uuid4().hex[:8]
            existing = next((s for s in self.subs if s["id"] == sid), None)
            merged = dict(existing or default_sub())
            for k in default_sub().keys():
                if k in fields:
                    merged[k] = fields[k]
            merged["id"] = sid
            if existing:
                c = self.connections.get(existing["connection"])
                if c and c.client and existing["topic"] and existing["topic"] != merged["topic"]:
                    try:
                        c.client.unsubscribe(existing["topic"])
                    except Exception:
                        pass
                self.subs[self.subs.index(existing)] = merged
            else:
                self.subs.append(merged)
            self.save()
            c = self.connections.get(merged["connection"])
            if merged["enabled"] and merged["topic"] and c and c.status == "Connected":
                c.client.subscribe(merged["topic"], qos=int(merged["qos"]))
                self.log("sub", f"[conn {merged['connection']}] subscribed {merged['topic']}")
            return merged

    def delete_sub(self, sid: str):
        with self.lock:
            s = next((x for x in self.subs if x["id"] == sid), None)
            if s:
                c = self.connections.get(s["connection"])
                if c and c.client and s["topic"]:
                    try:
                        c.client.unsubscribe(s["topic"])
                    except Exception:
                        pass
                self.subs.remove(s)
                self.save()


    def start_scheduler(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sim-scheduler")
        self._thread.start()
        for c in self.connections.values():
            if c.cfg["enabled"] and c.cfg["host"]:
                c.start()

    def shutdown(self):
        self._stop.set()
        for c in self.connections.values():
            c.stop(quiet=True)

    def _loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._tick(t0)
            except Exception as e:
                self.log("error", f"scheduler error: {e}")
            time.sleep(max(0.1, 1.0 - (time.time() - t0)))

    def _scope_values(self, b: dict) -> dict:
        rt = self.runtimes.get(b["device"])
        if not rt:
            return {}
        out = dict(rt.values())
        if self._wants_children(b):
            for ch in self.children_of(b["device"]):
                for k, v in ch.values().items():
                    out[f"{ch.cfg['name']}.{k}"] = v
        return out

    def _tick(self, now: float):
        with self.lock:
            for rt in self.runtimes.values():
                rt.tick()
            bindings = list(self.bindings)
        for b in bindings:
            if not b["enabled"] or b["device"] not in self.runtimes:
                continue
            st = self._bind_state.setdefault(b["id"], {"last_pub": 0.0, "last_values": {}})
            if b["mode"] == "periodic":
                if now - st["last_pub"] >= max(1, int(b["interval"])):
                    self._do_publish(b)
            elif b["mode"] == "cov":
                vals = self._scope_values(b)
                last = st["last_values"]
                changed = False
                for k, v in vals.items():
                    lv = last.get(k)
                    if lv is None:
                        changed = True
                        break
                    if isinstance(v, (int, float)) and isinstance(lv, (int, float)):
                        if abs(v - lv) >= float(b["cov_deadband"]):
                            changed = True
                            break
                    elif v != lv:
                        changed = True
                        break
                due_min = now - st["last_pub"] >= max(0, float(b["cov_min_interval"]))
                hb = float(b.get("cov_heartbeat") or 0)
                hb_due = hb > 0 and (now - st["last_pub"] >= hb)
                if (changed and due_min) or hb_due:
                    self._do_publish(b, reason="cov" if changed else "heartbeat")


    def _do_publish(self, b: dict, force=False, reason="") -> bool:
        rt = self.runtimes.get(b["device"])
        conn = self.connections.get(int(b["connection"]))
        if not rt or not conn:
            if force:
                self.log("error", f"[binding {b['id']}] missing device or connection")
            return False
        if conn.status != "Connected":
            if force:
                self.log("error", f"[{rt.cfg['name']}] connection {b['connection']} not connected")
            return False
        children = self.children_of(b["device"]) if self._wants_children(b) else []
        topic = b["topic"] or f"device/{rt.cfg['name']}/{rt.cfg['kind']}"
        payload = rt.render(b, children)
        ok = conn.publish(topic, payload, b["qos"], b["retain"])
        st = self._bind_state.setdefault(b["id"], {"last_pub": 0.0, "last_values": {}})
        if ok:
            st["last_pub"] = time.time()
            st["last_values"] = self._scope_values(b)
            tag = f" ({reason})" if reason else ""
            who = rt.cfg["name"] + (f" +{len(children)} sub" if children else "")
            self.log("pub", f"[conn {b['connection']}] {who} → {topic} q{b['qos']}{tag}  {payload[:160]}")
        else:
            self.log("error", f"[{rt.cfg['name']}] publish failed")
        return ok


    def state(self) -> dict:
        with self.lock:
            conns = []
            for i in sorted(self.connections):
                c = self.connections[i]
                conns.append({**c.cfg, "status": c.status, "started": c.started,
                              "stats": c.stats})
            devices = []
            for d in self.devices:
                rt = self.runtimes.get(d["id"])
                devices.append({**deepcopy(d),
                                "live_values": rt.display_values() if rt else {}})
            bindings = []
            for b in self.bindings:
                bindings.append({**b,
                                 "last_pub": self._bind_state.get(b["id"], {}).get("last_pub", 0)})
            return {
                "connections": conns,
                "devices": devices,
                "bindings": bindings,
                "subs": deepcopy(self.subs),
                "catalog": catalog(),
            }
