#!/usr/bin/env python3
"""
CS3001 Assignment-II: Go-Back-N and Selective Repeat over a simulated
unreliable satellite channel (R = 2 Mbps, RTT = 500 ms, L = 1000 bytes).

Design: a small discrete-event simulator. A priority queue holds events
(send, data arrival, ACK arrival, timeout) ordered by simulated time, so runs
are fast and exactly reproducible for a given --seed.

Slide terminology used:
  GBN sender  : base, nextseqnum, one timer (for base), cumulative ACKs
  GBN receiver: expectedseqnum, discards out-of-order packets
  SR sender   : window [base, base+N), per-packet timers, individual ACKs
  SR receiver : rcv_base, window [rcv_base, rcv_base+N), buffers out-of-order

Internally every packet has an absolute index (0,1,2,...) used only for
logging/statistics; the packets on the wire carry seq = index mod 2^k and all
protocol decisions use modular arithmetic, exactly as a real protocol must.
"""
import argparse
import heapq
import random
import struct
import sys

DATA, ACK = 0, 1


# --------------------------------------------------------------------------
# Packets and checksum
# --------------------------------------------------------------------------
def internet_checksum(kind, seq, payload):
    """16-bit one's-complement Internet checksum over type, seq and payload."""
    data = struct.pack("!BH", kind, seq) + payload
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


class Packet:
    def __init__(self, kind, seq, payload=b"", csum=None):
        self.kind = kind
        self.seq = seq
        self.payload = payload
        self.csum = internet_checksum(kind, seq, payload) if csum is None else csum

    def is_corrupt(self):
        return internet_checksum(self.kind, self.seq, self.payload) != self.csum

    def corrupted_copy(self, rng):
        """Flip one random bit but keep the old checksum (like a noisy link)."""
        if self.kind == DATA and self.payload:
            b = bytearray(self.payload)
            b[rng.randrange(len(b))] ^= 1 << rng.randrange(8)
            return Packet(self.kind, self.seq, bytes(b), self.csum)
        return Packet(self.kind, self.seq ^ (1 << rng.randrange(8)), self.payload, self.csum)


def payload_for(idx, size):
    return f"PKT{idx:06d}|".encode().ljust(size, b"x")[:size]


# --------------------------------------------------------------------------
# Simulator core + unreliable channel
# --------------------------------------------------------------------------
class Sim:
    def __init__(self, a):
        self.a = a
        self.rng = random.Random(a.seed)
        self.now = 0.0
        self.q = []
        self.counter = 0
        self.verbose = not a.quiet
        self.tx_time = a.size * 8 / a.rate      # L/R
        self.prop = a.rtt / 2                    # one-way propagation
        self.link_free = 0.0                     # sender's outgoing link busy until
        self.force_drop = set(a.force_drop or [])
        self.delivered = 0
        self.errors = 0
        self.sender = None
        self.receiver = None
        self.stats = dict(data_sent=0, retrans=0, acks_sent=0, timeouts=0,
                          data_lost=0, ack_lost=0, data_corrupted=0,
                          ack_corrupted=0, data_delayed=0, ack_delayed=0)

    # -- utilities --------------------------------------------------------
    def log(self, who, msg):
        if self.verbose:
            print(f"[t={self.now * 1000:9.1f} ms] {who:<8}| {msg}")

    def schedule(self, t, kind, arg):
        heapq.heappush(self.q, (t, self.counter, kind, arg))
        self.counter += 1

    # -- sending ----------------------------------------------------------
    def transmit(self, pkt, idx, retx):
        """Queue a DATA packet on the sender's link (one packet per L/R).
        Returns the time it actually leaves, so the caller can start its timer."""
        t = max(self.now, self.link_free)
        self.link_free = t + self.tx_time
        self.schedule(t, "send", (pkt, idx, retx))
        return t

    def do_send(self, pkt, idx, retx):
        st = self.stats
        st["data_sent"] += 1
        if retx:
            st["retrans"] += 1
        desc = f"pkt {idx} (seq={pkt.seq})"
        self.log("SENDER", f"{'RETRANSMIT' if retx else 'send':<10} DATA {desc}")
        forced = False
        if not retx and idx in self.force_drop:
            self.force_drop.discard(idx)
            forced = True
        self.channel(pkt, self.now + self.tx_time, "rx_data", "data", desc, forced)

    def send_ack(self, seq):
        self.stats["acks_sent"] += 1
        self.log("RECEIVER", f"send ACK{seq}")
        self.channel(Packet(ACK, seq), self.now, "rx_ack", "ack", f"ACK{seq}", False)

    # -- the unreliable channel ------------------------------------------
    def channel(self, pkt, depart, event, label, desc, forced_drop):
        a, rng, st = self.a, self.rng, self.stats
        if forced_drop or rng.random() < a.loss:
            st[f"{label}_lost"] += 1
            self.log("CHANNEL", f"*** LOSS      {label.upper()} {desc}")
            return
        if rng.random() < a.corrupt:
            pkt = pkt.corrupted_copy(rng)
            st[f"{label}_corrupted"] += 1
            self.log("CHANNEL", f"*** CORRUPT   {label.upper()} {desc}")
        delay = self.prop
        if rng.random() < a.delay:
            extra = rng.uniform(a.delay_max / 3, a.delay_max)
            delay += extra
            st[f"{label}_delayed"] += 1
            self.log("CHANNEL", f"*** DELAY     {label.upper()} {desc} (+{extra * 1000:.0f} ms)")
        self.schedule(depart + delay, event, pkt)

    def deliver(self, idx, payload):
        """Receiver hands data to the application; verify order and content."""
        if idx != self.delivered or payload != payload_for(idx, self.a.size):
            self.errors += 1
        self.delivered += 1
        self.log("RECEIVER", f"DELIVER pkt {idx} to application")


# --------------------------------------------------------------------------
# Go-Back-N
# --------------------------------------------------------------------------
class GBNSender:
    def __init__(self, sim, total, M, N):
        self.sim, self.total, self.M, self.N = sim, total, M, N
        self.base = 0
        self.nextseqnum = 0
        self.expiry = None          # the single timer (for base)

    def done(self):
        return self.base >= self.total

    def start(self):
        self.pump()

    def start_timer(self, t0):
        self.expiry = t0 + self.sim.a.timeout
        self.sim.schedule(self.expiry, "timeout", (self.expiry,))

    def pump(self):
        s = self.sim
        while self.nextseqnum < self.total and self.nextseqnum < self.base + self.N:
            idx = self.nextseqnum
            pkt = Packet(DATA, idx % self.M, payload_for(idx, s.a.size))
            t = s.transmit(pkt, idx, False)
            if self.base == self.nextseqnum:      # first packet in window -> start timer
                self.start_timer(t)
            self.nextseqnum += 1

    def on_ack(self, pkt):
        s = self.sim
        if pkt.is_corrupt():
            s.log("SENDER", "got corrupted ACK -> ignored")
            return
        off = (pkt.seq - self.base % self.M) % self.M
        if off < self.nextseqnum - self.base:      # cumulative ACK for base..base+off
            self.base += off + 1
            s.log("SENDER", f"got ACK{pkt.seq} (cumulative) -> base={self.base}, "
                            f"nextseqnum={self.nextseqnum}, window=[{self.base},{self.base + self.N - 1}]")
            if self.base == self.nextseqnum:
                self.expiry = None                  # nothing outstanding: stop timer
            else:
                self.start_timer(s.now)             # restart timer for new base
            self.pump()
        else:
            s.log("SENDER", f"got duplicate ACK{pkt.seq} -> ignored (base={self.base})")

    def on_timeout(self, expiry):
        s = self.sim
        if expiry != self.expiry or self.base == self.nextseqnum:
            return                                  # stale timer
        s.stats["timeouts"] += 1
        s.log("SENDER", f"TIMEOUT on base pkt {self.base} -> go back, resend "
                        f"pkts {self.base}..{self.nextseqnum - 1}")
        first = None
        for idx in range(self.base, self.nextseqnum):
            pkt = Packet(DATA, idx % self.M, payload_for(idx, s.a.size))
            t = s.transmit(pkt, idx, True)
            if first is None:
                first = t
        self.start_timer(first)


class GBNReceiver:
    def __init__(self, sim, M):
        self.sim, self.M = sim, M
        self.expectedseqnum = 0

    def ack_last(self):
        if self.expectedseqnum > 0:
            self.sim.send_ack((self.expectedseqnum - 1) % self.M)

    def on_data(self, pkt):
        s = self.sim
        if pkt.is_corrupt():
            s.log("RECEIVER", "got corrupted DATA -> discarded, re-ACK last in-order")
            self.ack_last()
            return
        if pkt.seq == self.expectedseqnum % self.M:
            s.log("RECEIVER", f"got DATA seq={pkt.seq} in order (expectedseqnum={self.expectedseqnum})")
            s.deliver(self.expectedseqnum, pkt.payload)
            self.expectedseqnum += 1
            s.send_ack(pkt.seq)
        else:
            s.log("RECEIVER", f"got DATA seq={pkt.seq} OUT OF ORDER (expected "
                              f"{self.expectedseqnum % self.M}) -> discarded")
            self.ack_last()


# --------------------------------------------------------------------------
# Selective Repeat
# --------------------------------------------------------------------------
class SRSender:
    def __init__(self, sim, total, M, N):
        self.sim, self.total, self.M, self.N = sim, total, M, N
        self.base = 0                # send_base
        self.nextseqnum = 0
        self.acked = set()           # indices ACKed but above base
        self.timers = {}             # idx -> expiry (one timer per packet)

    def done(self):
        return self.base >= self.total

    def start(self):
        self.pump()

    def start_timer(self, idx, t0):
        exp = t0 + self.sim.a.timeout
        self.timers[idx] = exp
        self.sim.schedule(exp, "timeout", (idx, exp))

    def pump(self):
        s = self.sim
        while self.nextseqnum < self.total and self.nextseqnum < self.base + self.N:
            idx = self.nextseqnum
            pkt = Packet(DATA, idx % self.M, payload_for(idx, s.a.size))
            t = s.transmit(pkt, idx, False)
            self.start_timer(idx, t)
            self.nextseqnum += 1

    def on_ack(self, pkt):
        s = self.sim
        if pkt.is_corrupt():
            s.log("SENDER", "got corrupted ACK -> ignored")
            return
        off = (pkt.seq - self.base % self.M) % self.M
        if off < self.nextseqnum - self.base:
            idx = self.base + off
            if idx in self.acked:
                s.log("SENDER", f"got duplicate ACK{pkt.seq} (pkt {idx} already ACKed)")
                return
            self.acked.add(idx)
            self.timers.pop(idx, None)
            s.log("SENDER", f"got ACK{pkt.seq} -> pkt {idx} marked ACKed")
            old = self.base
            while self.base in self.acked:
                self.acked.discard(self.base)
                self.base += 1
            if self.base != old:
                s.log("SENDER", f"window slides: send_base {old} -> {self.base}, "
                                f"window=[{self.base},{self.base + self.N - 1}]")
            self.pump()
        else:
            s.log("SENDER", f"got stale ACK{pkt.seq} (outside window) -> ignored")

    def on_timeout(self, idx, exp):
        s = self.sim
        if self.timers.get(idx) != exp:
            return                                  # timer cancelled or restarted
        s.stats["timeouts"] += 1
        s.log("SENDER", f"TIMEOUT on pkt {idx} -> resend only this packet")
        pkt = Packet(DATA, idx % self.M, payload_for(idx, s.a.size))
        t = s.transmit(pkt, idx, True)
        self.start_timer(idx, t)


class SRReceiver:
    def __init__(self, sim, M, N):
        self.sim, self.M, self.N = sim, M, N
        self.rcv_base = 0
        self.buffer = {}             # idx -> payload

    def on_data(self, pkt):
        s = self.sim
        if pkt.is_corrupt():
            s.log("RECEIVER", "got corrupted DATA -> discarded (no ACK)")
            return
        off = (pkt.seq - self.rcv_base % self.M) % self.M
        if off < self.N:                                # inside receiver window
            idx = self.rcv_base + off
            if idx in self.buffer:
                s.log("RECEIVER", f"got duplicate DATA seq={pkt.seq} (already buffered)")
                s.send_ack(pkt.seq)
                return
            self.buffer[idx] = pkt.payload
            if off == 0:
                s.log("RECEIVER", f"got DATA seq={pkt.seq} = rcv_base")
            else:
                s.log("RECEIVER", f"got DATA seq={pkt.seq} out of order -> BUFFERED "
                                  f"(rcv_base={self.rcv_base})")
            s.send_ack(pkt.seq)                          # individual ACK
            old = self.rcv_base
            while self.rcv_base in self.buffer:          # deliver in-order run
                s.deliver(self.rcv_base, self.buffer.pop(self.rcv_base))
                self.rcv_base += 1
            if self.rcv_base != old:
                s.log("RECEIVER", f"rcv_base {old} -> {self.rcv_base}, "
                                  f"window=[{self.rcv_base},{self.rcv_base + self.N - 1}]")
        elif off >= self.M - self.N:                     # inside previous window
            s.log("RECEIVER", f"got old DATA seq={pkt.seq} (already delivered) -> re-ACK")
            s.send_ack(pkt.seq)
        else:
            s.log("RECEIVER", f"got DATA seq={pkt.seq} outside both windows -> ignored")


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(description="GBN / SR over a simulated unreliable channel")
    p.add_argument("--protocol", choices=["gbn", "sr"], required=True)
    p.add_argument("--packets", type=int, default=200, help="number of data packets")
    p.add_argument("--window", type=int, default=4, help="window size N")
    p.add_argument("--bits", type=int, default=3, help="sequence number bits k (mod 2^k)")
    p.add_argument("--loss", type=float, default=0.0, help="loss probability (data and ACKs)")
    p.add_argument("--corrupt", type=float, default=0.0, help="corruption probability")
    p.add_argument("--delay", type=float, default=0.0, help="extra-delay probability")
    p.add_argument("--delay-max", type=float, default=0.2, help="max extra delay in s")
    p.add_argument("--timeout", type=float, default=None,
                   help="retransmission timeout in s (default: auto = RTT + 2*delay_max + 0.1 "
                        "when delay>0, else RTT + 0.1)")
    p.add_argument("--seed", type=int, default=1234, help="random seed (last 4 digits of roll no.)")
    p.add_argument("--size", type=int, default=1000, help="packet size L in bytes")
    p.add_argument("--rate", type=float, default=2e6, help="link rate R in bits/s")
    p.add_argument("--rtt", type=float, default=0.5, help="round-trip propagation in s")
    p.add_argument("--force-drop", type=int, nargs="*", default=[],
                   help="packet indices whose FIRST transmission is dropped (for demos)")
    p.add_argument("--force", action="store_true",
                   help="allow window sizes that violate the safe-window rule")
    p.add_argument("--quiet", action="store_true", help="no per-event log, summary only")
    p.add_argument("--max-events", type=int, default=5_000_000)
    return p


def run(a):
    M = 2 ** a.bits
    limit = M - 1 if a.protocol == "gbn" else M // 2
    if a.window > limit:
        msg = (f"window N={a.window} exceeds the safe maximum {limit} for "
               f"{a.protocol.upper()} with {a.bits}-bit sequence numbers (2^k={M})")
        if not a.force:
            sys.exit("ERROR: " + msg + "  (use --force to run anyway)")
        print("WARNING: " + msg, file=sys.stderr)

    safe_to = a.rtt + (2 * a.delay_max if a.delay > 0 else 0.0)
    if a.timeout is None:
        a.timeout = safe_to + 0.1
    elif a.timeout < safe_to:
        print(f"WARNING: timeout {a.timeout}s < worst-case round trip {safe_to}s. Premature "
              f"timeouts create duplicates; with {a.bits}-bit sequence numbers a stale duplicate "
              f"ACK can be mistaken for a new one and the transfer may FAIL.", file=sys.stderr)

    sim = Sim(a)
    if a.protocol == "gbn":
        sim.sender = GBNSender(sim, a.packets, M, a.window)
        sim.receiver = GBNReceiver(sim, M)
    else:
        sim.sender = SRSender(sim, a.packets, M, a.window)
        sim.receiver = SRReceiver(sim, M, a.window)

    sim.sender.start()
    events = 0
    while sim.q and not sim.sender.done() and events < a.max_events:
        t, _, kind, arg = heapq.heappop(sim.q)
        sim.now = t
        if kind == "send":
            sim.do_send(*arg)
        elif kind == "rx_data":
            sim.receiver.on_data(arg)
        elif kind == "rx_ack":
            sim.sender.on_ack(arg)
        elif kind == "timeout":
            sim.sender.on_timeout(*arg)
        events += 1

    st = sim.stats
    t_total = sim.now
    ok = sim.sender.done() and sim.delivered == a.packets and sim.errors == 0
    res = dict(protocol=a.protocol.upper(), packets=a.packets, transmitted=st["data_sent"],
               retransmissions=st["retrans"], delivered=sim.delivered, correct=ok,
               time=t_total, errors=sim.errors, timeouts=st["timeouts"],
               acks_sent=st["acks_sent"], data_lost=st["data_lost"], ack_lost=st["ack_lost"],
               data_corrupted=st["data_corrupted"], ack_corrupted=st["ack_corrupted"],
               data_delayed=st["data_delayed"], ack_delayed=st["ack_delayed"])
    res["throughput_kbps"] = (sim.delivered * a.size * 8 / t_total / 1000) if t_total else 0.0
    res["utilization"] = (sim.delivered * sim.tx_time / t_total) if t_total else 0.0
    return res


def print_summary(r, a):
    print("\n" + "=" * 60)
    print(f" {r['protocol']}  |  N={a.window}, k={a.bits}, seed={a.seed}, "
          f"loss={a.loss}, corrupt={a.corrupt}, delay={a.delay}")
    print("=" * 60)
    print(f" Packets to send            : {r['packets']}")
    print(f" Data packets transmitted   : {r['transmitted']}")
    print(f" Retransmissions            : {r['retransmissions']}")
    print(f" Delivered in order, correct: {r['delivered']}  (verified: {r['correct']})")
    print(f" Timeouts                   : {r['timeouts']}")
    print(f" Channel: data lost/corrupt/delayed = {r['data_lost']}/{r['data_corrupted']}/{r['data_delayed']}")
    print(f"          ACKs  lost/corrupt/delayed = {r['ack_lost']}/{r['ack_corrupted']}/{r['ack_delayed']}"
          f"  (ACKs sent: {r['acks_sent']})")
    print(f" Timeout used               : {a.timeout:.2f} s")
    print(f" Completion time (simulated): {r['time']:.3f} s")
    print(f" Throughput (goodput)       : {r['throughput_kbps']:.1f} kbps")
    print(f" Link utilization           : {r['utilization'] * 100:.2f} %")
    if not r["correct"]:
        print(" !! TRANSFER FAILED OR DATA WRONG (see log)")


def main():
    a = build_parser().parse_args()
    r = run(a)
    print_summary(r, a)
    return 0 if r["correct"] else 1


if __name__ == "__main__":
    sys.exit(main())
