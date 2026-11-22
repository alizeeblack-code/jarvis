import AVFoundation
import UIKit

// MARK: - What /api/desk sends

struct DeskItem: Decodable, Equatable {
    var sid = "", title = "", project = "", agent = "", state = "idle", step = "", err = ""
    var bg = 0
    var at: Double = 0          // ms, server clock

    /// running with only background work left counts as its own look
    var shown: String { state == "run" && bg > 0 ? "bg" : state }
    var needsYou: Bool { ["wait", "error", "limit"].contains(state) }

    enum K: String, CodingKey { case sid, title, project, agent, state, step, err, bg, at }
    init(from d: Decoder) throws {
        let c = try d.container(keyedBy: K.self)
        sid = (try? c.decodeIfPresent(String.self, forKey: .sid)) ?? ""
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        project = (try? c.decodeIfPresent(String.self, forKey: .project)) ?? ""
        agent = (try? c.decodeIfPresent(String.self, forKey: .agent)) ?? ""
        state = (try? c.decodeIfPresent(String.self, forKey: .state)) ?? "idle"
        step = (try? c.decodeIfPresent(String.self, forKey: .step)) ?? ""
        err = (try? c.decodeIfPresent(String.self, forKey: .err)) ?? ""
        bg = (try? c.decodeIfPresent(Int.self, forKey: .bg)) ?? 0
        at = (try? c.decodeIfPresent(Double.self, forKey: .at)) ?? 0
    }
    init() {}
}

struct DeskCounts: Decodable, Equatable {
    var wait = 0, run = 0, bg = 0, done = 0, error = 0
}

struct Wash: Equatable {
    let kind: String
    let id: Int
}

/// what is left of one agent's quota (its tightest window)
struct DeskUsage: Decodable, Equatable {
    var name: String
    var left: Int
    var resets_at: Double       // ms; 0 when nothing is used
    var at: Double              // ms: when the numbers were last read
}

struct DeskPayload: Decodable {
    var at: Double = 0
    var counts = DeskCounts()
    var items: [DeskItem] = []
    var usage: [DeskUsage]? = nil
}

// MARK: - Text

let chinese = Locale.preferredLanguages.first?.hasPrefix("zh") == true
func tr(_ zh: String, _ en: String) -> String { chinese ? zh : en }

enum Ages {
    static func dur(_ s: TimeInterval) -> String {
        let m = max(0, Int(s / 60))
        if m < 1 { return tr("<1分钟", "<1m") }
        if m < 60 { return tr("\(m) 分钟", "\(m)m") }
        let h = m / 60, r = m % 60
        if h >= 24 { return tr("\(h / 24) 天", "\(h / 24)d") }
        return r > 0 && h < 10 ? tr("\(h) 小时 \(r) 分", "\(h)h \(r)m") : tr("\(h) 小时", "\(h)h")
    }
    /// the short form for a key: "18 分", "2 小时", "3 天"
    static func short(_ s: TimeInterval) -> String {
        let m = max(0, Int(s / 60))
        if m < 1 { return tr("<1 分", "<1m") }
        if m < 60 { return tr("\(m) 分", "\(m)m") }
        let h = m / 60
        return h < 24 ? tr("\(h) 小时", "\(h)h") : tr("\(h / 24) 天", "\(h / 24)d")
    }
    static func ago(_ s: TimeInterval) -> String {
        s < 60 ? tr("刚刚", "just now") : tr(dur(s) + "前", dur(s) + " ago")
    }
}

// MARK: - The store: polls Jarvis, works out alerts, dims the screen when nothing needs you

@MainActor
final class DeskStore: ObservableObject {
    static let urlKey = "deskURL"
    static let pollEvery: TimeInterval = 4
    static let staleAfter: TimeInterval = 20      // no answer this long: say so instead of showing old states
    static let repeatEvery: TimeInterval = 5 * 60 // a turn still waiting on you chimes again this often
    static let dimAfter: TimeInterval = 10 * 60   // nothing new for this long: lower the screen

    @Published private(set) var payload: DeskPayload?
    @Published private(set) var failure = ""
    @Published private(set) var now = Date()
    /// a soft wash over the screen when something turns to you, fails or finishes
    @Published private(set) var wash: Wash?

    private var gotAt = Date.distantPast
    private(set) var okAt: Date?
    private var prev: [String: String]?            // sid -> state at the previous poll
    private let firstAt = Date()
    private var unacked: [String: String] = [:]    // sid -> "wait" | "error"
    private var lastChime = Date.distantPast
    private var lastAttention = Date()
    private var userBrightness: CGFloat?
    private var players: [String: AVAudioPlayer] = [:]
    private var pollTask: Task<Void, Never>?
    private var ticker: Timer?
    private var washCount = 0

    // MARK: address

    var address: URL? {
        let saved = UserDefaults.standard.string(forKey: Self.urlKey)
        let built = Bundle.main.object(forInfoDictionaryKey: "JarvisDeskURL") as? String
        for s in [saved, built] {
            if let s = s?.trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty,
               let u = URL(string: s), u.scheme?.hasPrefix("http") == true, u.host != nil { return u }
        }
        return nil
    }

    func setAddress(_ s: String) {
        UserDefaults.standard.set(s.trimmingCharacters(in: .whitespacesAndNewlines), forKey: Self.urlKey)
        payload = nil; okAt = nil; prev = nil; failure = ""
        restart()
    }

    /// the link carries the token (`?token=`); the API gets it as a header
    private func request(_ path: String = "/api/desk") -> URLRequest? {
        guard let link = address, var c = URLComponents(url: link, resolvingAgainstBaseURL: false) else { return nil }
        let token = c.queryItems?.first(where: { $0.name == "token" })?.value
        c.percentEncodedPath = path; c.query = nil; c.fragment = nil
        guard let url = c.url else { return nil }
        var r = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 8)
        if let token = token, !token.isEmpty { r.setValue(token, forHTTPHeaderField: "X-Jarvis-Token") }
        return r
    }

    // MARK: life cycle

    func start() {
        loadSounds()
        if userBrightness == nil { userBrightness = UIScreen.main.brightness }
        lastAttention = Date()
        ticker?.invalidate()
        ticker = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        restart()
    }

    func stop() {
        pollTask?.cancel(); pollTask = nil
        ticker?.invalidate(); ticker = nil
        if let b = userBrightness { UIScreen.main.brightness = b }   // give the phone its brightness back
        userBrightness = nil
    }

    private func restart() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.poll()
                try? await Task.sleep(nanoseconds: UInt64(Self.pollEvery * 1_000_000_000))
            }
        }
    }

    private func poll() async {
        guard let req = request() else {
            failure = tr("两指按住设置地址", "Hold with two fingers to set the address")
            tick(); return
        }
        do {
            let (data, res) = try await URLSession.shared.data(for: req)
            let code = (res as? HTTPURLResponse)?.statusCode ?? 0
            guard code == 200 else {
                failure = code == 401 ? tr("访问令牌不对", "Wrong access token") : "HTTP \(code)"
                tick(); return
            }
            let p = try JSONDecoder().decode(DeskPayload.self, from: data)
            payload = p; gotAt = Date(); okAt = gotAt; failure = ""
            compare(p.items)
        } catch is CancellationError {
            return
        } catch {
            failure = (error as? URLError)?.code == .timedOut ? tr("超时", "timed out") : (address?.host ?? "")
        }
        tick()
    }

    // MARK: reading the state

    var stale: Bool { okAt.map { now.timeIntervalSince($0) > Self.staleAfter } ?? true }
    var items: [DeskItem] { payload?.items ?? [] }

    /// seconds since the item last changed, on the server's clock
    func age(_ it: DeskItem) -> TimeInterval {
        guard let p = payload else { return 0 }
        return (p.at - it.at) / 1000 + now.timeIntervalSince(gotAt)
    }

    private func tick() {
        now = Date()
        if !unacked.isEmpty { lastAttention = now }
        if unacked.values.contains("wait"), now.timeIntervalSince(lastChime) >= Self.repeatEvery { chime("wait") }
        applyBrightness()
    }

    // MARK: alerts

    private func compare(_ items: [DeskItem]) {
        var cur: [String: String] = [:]
        items.forEach { cur[$0.sid] = $0.state }
        // whatever no longer needs you stops on its own
        for sid in unacked.keys where !["wait", "error", "limit"].contains(cur[sid] ?? "") { unacked[sid] = nil }
        var sound = ""
        if let prev = prev, let p = payload {
            for it in items where !it.sid.isEmpty && prev[it.sid] != it.state {
                // a session seen for the first time only counts when it changed after the app opened
                if prev[it.sid] == nil && (p.at - it.at) / 1000 > Date().timeIntervalSince(firstAt) { continue }
                switch it.state {
                case "wait":
                    unacked[it.sid] = "wait"; if sound != "error" { sound = "wait" }
                case "error", "limit":
                    unacked[it.sid] = "error"; sound = "error"
                case "done" where prev[it.sid] != nil && prev[it.sid] != "idle":
                    if sound.isEmpty { sound = "done" }
                default: break
                }
            }
        }
        prev = cur
        if !sound.isEmpty {
            chime(sound)
            washCount += 1
            wash = Wash(kind: sound, id: washCount)
            lastAttention = Date()
        }
    }

    // MARK: open a session on the Mac

    /// what a key says for a few seconds after it was tapped
    @Published private(set) var notes: [String: String] = [:]

    /// a tap on a key: wake the Mac and open that session where it lives (after unlock, if locked)
    func reveal(_ it: DeskItem) {
        acknowledge()
        let sid = it.sid
        let path = "/api/sessions/" + (sid.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-_.:"))) ?? sid) + "/reveal"
        guard !sid.isEmpty, var r = request(path) else { return }
        r.httpMethod = "POST"
        notes[sid] = tr("正在打开…", "Opening…")
        r.timeoutInterval = 30     // a locked Mac answers only after the password was typed
        Task {
            struct Answer: Decodable { var ok = false; var how = ""; var locked = false; var unlocked: Bool? }
            var text = tr("打不开", "Couldn't open")
            var hold: TimeInterval = 4
            if let (data, res) = try? await URLSession.shared.data(for: r),
               (res as? HTTPURLResponse)?.statusCode == 200,
               let a = try? JSONDecoder().decode(Answer.self, from: data), a.ok {
                if a.locked {
                    text = tr("解锁 Mac 后打开", "Unlock the Mac"); hold = 12
                } else if a.unlocked == true {
                    text = tr("已解锁并打开", "Unlocked and opened")
                } else {
                    switch a.how {
                    case "jarvis": text = tr("已打开 Jarvis", "Opened Jarvis")
                    case "app": text = tr("已切到 Mac", "Switched on the Mac")
                    default: text = tr("已在 Mac 打开", "Opened on the Mac")
                    }
                }
            }
            notes[sid] = text
            try? await Task.sleep(nanoseconds: UInt64(hold * 1_000_000_000))
            if notes[sid] == text { notes[sid] = nil }
        }
    }

    /// a tap: stop repeating the chime and wake the screen
    func acknowledge() {
        unacked.removeAll()
        lastAttention = Date()
        applyBrightness()
    }

    // MARK: sound

    private func loadSounds() {
        guard players.isEmpty else { return }
        for k in ["wait", "error", "done"] {
            if let u = Bundle.main.url(forResource: k, withExtension: "wav"),
               let p = try? AVAudioPlayer(contentsOf: u) {
                p.prepareToPlay()
                players[k] = p
            }
        }
    }

    private func chime(_ kind: String) {
        lastChime = Date()
        guard let p = players[kind] else { return }
        p.currentTime = 0
        p.play()
    }

    // MARK: brightness: bright while something is new or needs you, low otherwise, lower at night

    private func applyBrightness() {
        guard let user = userBrightness else { return }
        let hour = Calendar.current.component(.hour, from: now)
        let night = hour >= 22 || hour < 7
        let quiet = now.timeIntervalSince(lastAttention) > Self.dimAfter
        var target = user
        if night { target = min(target, 0.3) }
        if quiet { target = min(target, night ? 0.05 : 0.2) }
        if abs(UIScreen.main.brightness - target) > 0.01 { UIScreen.main.brightness = target }
    }
}
