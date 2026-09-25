// Copyright 2026 iPhoneBridge contributors. SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <vector>

namespace ipbm {
constexpr uint32_t MaxBody = 16777216;
constexpr uint32_t MaxClientBody = 40;
constexpr size_t MaxConnections = 4, MaxPendingCommands = 32;
constexpr size_t MaxQueuedBytes = 20 * 1024 * 1024;
constexpr size_t VideoHighWater = 512 * 1024;
enum Type : uint8_t { Hello=1, Format=2, Video=3, Still=4, GeometryMessage=5,
    Ack=6, Error=7, Pong=8, Stats=9, Subscribe=16, RequestKeyframe=17,
    RequestStill=18, GetGeometry=19, AcquireInput=20, ReleaseInput=21,
    Pointer=22, Key=23, Button=24, Ping=25, GetStats=26 };
using Bytes = std::vector<uint8_t>;
inline uint32_t read32(const uint8_t *p) {
    return (uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];
}
inline void put32(Bytes& b, uint32_t x) {
    b.insert(b.end(), {uint8_t(x>>24), uint8_t(x>>16), uint8_t(x>>8), uint8_t(x)});
}
inline void put64(Bytes& b, uint64_t x) { put32(b, uint32_t(x>>32)); put32(b, uint32_t(x)); }
inline Bytes message(Type type, uint32_t request, const Bytes& payload={}) {
    if (payload.size() > MaxBody-8) throw std::length_error("protocol payload too large");
    Bytes out; out.reserve(payload.size()+12); put32(out, uint32_t(payload.size()+8));
    out.insert(out.end(), {uint8_t(type),0,0,0}); put32(out,request);
    out.insert(out.end(),payload.begin(),payload.end()); return out;
}
struct Command {
    uint8_t type; uint32_t request; Bytes payload;
    uint64_t receivedNs=0; // Local parser-completion timestamp; never serialized on the wire.
};
// Client requests are deliberately small. Validate the declared size before buffering.
class RequestParser {
    Bytes pending_;
public:
    template<class Consume> bool feed(const uint8_t *data, size_t size, Consume consume) {
        for (size_t i=0;i<size;++i) {
            pending_.push_back(data[i]);
            if (pending_.size()<4) continue;
            uint32_t length=read32(pending_.data());
            if (length<8 || length>MaxClientBody) return false;
            if (pending_.size()!=size_t(length)+4) continue;
            if (pending_[5] || pending_[6] || pending_[7] || !read32(pending_.data()+8)) return false;
            Command c{pending_[4],read32(pending_.data()+8),{pending_.begin()+12,pending_.end()}};
            pending_.clear(); if (!consume(std::move(c))) return false;
        }
        return true;
    }
    size_t buffered() const { return pending_.size(); }
};
struct Geometry {
    uint32_t width=0,height=0,turns=0,generation=1;
    uint32_t displayWidth() const { return (turns&1)?height:width; }
    uint32_t displayHeight() const { return (turns&1)?width:height; }
    bool point(uint32_t x,uint32_t y,uint32_t& px,uint32_t& py) const {
        if (x>=displayWidth() || y>=displayHeight()) return false;
        switch(turns) {
        case 0: px=x; py=y; break;
        case 1: px=y; py=height-1-x; break;
        case 2: px=width-1-x; py=height-1-y; break;
        case 3: px=width-1-y; py=x; break;
        default: return false;
        } return true;
    }
    Bytes bytes() const { Bytes b; for(auto x:{width,height,turns,generation}) put32(b,x); return b; }
    void changed() { if (++generation==0) generation=1; }
};
class InputLease {
    uint64_t owner_=0, touched_=0;
public:
    static constexpr uint64_t Timeout=25000000000ULL;
    bool acquire(uint64_t id,uint64_t now) {
        if (!id || (owner_ && owner_!=id)) return false;
        owner_=id; touched_=now; return true;
    }
    bool touch(uint64_t id,uint64_t now) {
        if (!id || owner_!=id) return false; touched_=now; return true;
    }
    bool release(uint64_t id) { if (!id || owner_!=id) return false; owner_=0; return true; }
    bool expired(uint64_t now) const { return owner_ && now-touched_>=Timeout; }
    uint64_t owner() const { return owner_; }
};
// Net queue depth measures congestion, not progress: a paced reader can keep a
// stable/growing queue while successful socket writes continue indefinitely.
class WriteProgress {
    uint64_t written_=0,since_=0;
public:
    static constexpr uint64_t Timeout=5000000000ULL;
    bool stalled(size_t queued,uint64_t written,uint64_t now) {
        if(!queued || written!=written_ || !since_) since_=now;
        written_=written;
        return queued && now-since_>=Timeout;
    }
};
// Shared by the capture surface and encoder token; only the final owner releases.
struct SlotLease {
    std::atomic<bool> busy{false};
    bool claim() { bool free=false; return busy.compare_exchange_strong(free,true); }
    void release() { busy.store(false); }
};
struct SlotClaim {
    std::shared_ptr<SlotLease> slot;
    explicit SlotClaim(std::shared_ptr<SlotLease> s):slot(std::move(s)) {}
    ~SlotClaim() { slot->release(); }
};
} // namespace ipbm
