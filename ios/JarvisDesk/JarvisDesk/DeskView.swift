import SwiftUI

// MARK: - Palette: muted, frosted, nothing near pure white (easy on the eyes on a desk all day).
// Running is a soft mint; done, a warm white, so the two never read alike.

struct Tone {
    let r: Double, g: Double, b: Double
    init(_ r: Double, _ g: Double, _ b: Double) { self.r = r; self.g = g; self.b = b }
    func a(_ alpha: Double) -> Color { Color(red: r / 255, green: g / 255, blue: b / 255).opacity(alpha) }
    /// the state colour lifted a little, for text on the dark background
    var text: Color { Color(red: min(255, r * 1.25) / 255, green: min(255, g * 1.25) / 255, blue: min(255, b * 1.25) / 255) }

    static let run = Tone(112, 190, 156), wait = Tone(214, 150, 72), done = Tone(176, 168, 150)
    static let idle = Tone(120, 118, 112), error = Tone(204, 96, 90), off = Tone(40, 40, 40)

    static func of(_ state: String) -> Tone {
        switch state {
        case "run", "bg", "quiet": return .run
        case "wait": return .wait
        case "done": return .done
        case "idle": return .idle
        case "error", "limit": return .error
        default: return .off
        }
    }
}

extension Color {
    static let deskBG = Color(red: 5 / 255, green: 5 / 255, blue: 5 / 255)
    static let deskFG = Color(red: 0xd9 / 255, green: 0xd5 / 255, blue: 0xcc / 255)
    static let deskDim = Color(red: 0x6f / 255, green: 0x6c / 255, blue: 0x66 / 255)
}

/// Two rhythms: what needs you swells every 3.2 s; what is running breathes, slower and fainter, every 6 s
/// (all in step, as one machine). Everything else stays still (and costs nothing).
enum Breath {
    case still, running, needsYou

    static func of(_ state: String) -> Breath {
        switch state {
        case "wait", "error", "limit": return .needsYou
        case "run", "bg": return .running
        default: return .still
        }
    }
    var period: Double { self == .needsYou ? 3.2 : 6 }
}

struct Pulse<Content: View>: View {
    let breath: Breath
    @ViewBuilder let content: (Double) -> Content     // 0…1, 0 when still
    var body: some View {
        if breath == .still {
            content(0)
        } else {
            TimelineView(.animation(minimumInterval: breath == .needsYou ? 1 / 30 : 1 / 20)) { ctx in
                let t = ctx.date.timeIntervalSinceReferenceDate
                content((1 - cos(2 * .pi * t / breath.period)) / 2)
            }
        }
    }
}

// MARK: - Screen

struct DeskView: View {
    @ObservedObject var store: DeskStore
    @State private var washOpacity = 0.0
    @State private var washTone = Tone.done

    var body: some View {
        GeometryReader { geo in
            let pad = EdgeInsets(top: 14, leading: 16, bottom: 14, trailing: 16)
            let h = geo.size.height - pad.top - pad.bottom
            // the clock above the lamp and the quota below it take a third of the height
            let lamp = min(200, h * 0.52)
            HStack(spacing: 16) {
                Hero(store: store, size: lamp, height: h)
                    .frame(width: max(lamp + 40, 240))
                Keys(store: store, height: h)
                    .opacity(store.stale ? 0.35 : 1)
            }
            .padding(pad)
        }
        .background(Color.deskBG.ignoresSafeArea())
        .overlay(washTone.a(washOpacity).ignoresSafeArea().allowsHitTesting(false))
        .contentShape(Rectangle())
        .onTapGesture { store.acknowledge() }
        .onChange(of: store.wash) { w in
            guard let w = w else { return }
            washTone = Tone.of(w.kind == "error" ? "error" : w.kind)
            withAnimation(.easeOut(duration: 0.4)) { washOpacity = 0.16 }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                withAnimation(.easeIn(duration: 1.6)) { washOpacity = 0 }
            }
        }
    }
}

// MARK: - The big lamp: the one thing that most needs you, or else what is going on

struct Hero: View {
    @ObservedObject var store: DeskStore
    let size: CGFloat
    let height: CGFloat

    private struct Look {
        var state: String; var word: String; var since = ""; var who = ""; var item: DeskItem? = nil
        var count: Int? = nil       // running / waiting: how many, the biggest thing on the lamp
    }

    private var look: Look {
        if store.stale {
            return Look(state: "off", word: store.okAt == nil ? tr("连接中", "connecting") : tr("连不上", "offline"),
                        since: store.okAt.map { Ages.ago(store.now.timeIntervalSince($0)) } ?? "",
                        who: store.failure)
        }
        let items = store.items
        let by = { (sts: [String]) in items.filter { sts.contains($0.state) } }
        let err = by(["error", "limit"]), wait = by(["wait"]), run = by(["run", "quiet"]), done = by(["done"])
        if let e = err.first {
            return Look(state: "error", word: tr("出错了", "error"),
                        since: e.state == "limit" ? tr("额度用完", "no quota") : "", who: e.project, item: e)
        }
        let c = store.payload?.counts
        if let w = wait.first {
            let n = max(c?.wait ?? 0, wait.count)
            return Look(state: "wait", word: tr("轮到你", "your turn"), since: Ages.dur(store.age(w)),
                        who: w.project, item: w, count: n)
        }
        if let r = run.first {
            let n = max((c?.run ?? 0) + (c?.bg ?? 0), run.count)
            return Look(state: "run", word: tr("运行中", "running"), who: n > 1 ? "" : r.project, item: r, count: n)
        }
        if let d = done.first {
            return Look(state: "done", word: tr("完成了", "done"), since: Ages.ago(store.age(d)), who: d.project, item: d)
        }
        return Look(state: "idle", word: tr("空闲", "idle"))
    }

    private var clock: String {
        let f = DateFormatter(); f.dateFormat = "H:mm"
        return f.string(from: store.now)
    }

    /// after a tap, the lamp says what happened on the Mac for a few seconds
    private var shown: Look {
        var l = look
        if let it = l.item, let n = store.notes[it.sid] {
            if l.count != nil { l.word = n; l.since = "" } else { l.since = n }
        }
        return l
    }

    var body: some View {
        let l = shown
        let tone = Tone.of(l.state)
        let k = size / 240
        let hk = min(1.1, height / 386)          // 386 pt: the 6S Plus in landscape
        VStack(spacing: 10 * hk) {
            Text(clock)
                .font(.system(size: 46 * hk, weight: .semibold, design: .rounded).monospacedDigit())
                .foregroundColor(.deskFG)
                .lineLimit(1)
            Pulse(breath: Breath.of(l.state)) { p in
                ZStack {
                    Circle()
                        .fill(RadialGradient(stops: [.init(color: tone.a(0.30), location: 0),
                                                     .init(color: tone.a(0.22), location: 0.55),
                                                     .init(color: tone.a(0.10), location: 1)],
                                             center: .center, startRadius: 0, endRadius: size / 2))
                        .opacity(Breath.of(l.state) == .still ? 1 : 0.55 + 0.45 * p)
                        .shadow(color: tone.a(0.16 + 0.16 * p), radius: 14 + 12 * p)
                    Circle()   // the light caught inside the rim
                        .stroke(tone.a(0.14 + 0.08 * p), lineWidth: 26)
                        .blur(radius: 14)
                        .clipShape(Circle())
                    Circle().strokeBorder(tone.a(0.45), lineWidth: 1.5)
                    if let n = l.count {
                        // how many, big; what they are doing, small
                        VStack(spacing: 2 * k) {
                            Text("\(n)")
                                .font(.system(size: 112 * k, weight: .heavy, design: .rounded).monospacedDigit())
                                .foregroundColor(tone.text)
                                .lineLimit(1).minimumScaleFactor(0.5)
                            Text(l.since.isEmpty ? l.word : l.word + " · " + l.since)
                                .font(.system(size: 26 * k, weight: .bold).monospacedDigit())
                                .foregroundColor(.deskFG)
                                .lineLimit(1).minimumScaleFactor(0.5)
                                .padding(.horizontal, 14 * k)     // the circle is narrower down here
                        }
                        .padding(.horizontal, 22 * k)
                    } else {
                    VStack(spacing: 10 * k) {
                        Text(l.word)
                            .font(.system(size: (chinese ? 50 : 40) * k, weight: .heavy))
                            .foregroundColor(l.state == "off" ? .deskDim : tone.text)
                            .lineLimit(1).minimumScaleFactor(0.6)
                        if !l.since.isEmpty {
                            Text(l.since)
                                .font(.system(size: 27 * k, weight: .bold).monospacedDigit())
                                .foregroundColor(.deskFG)
                                .lineLimit(1).minimumScaleFactor(0.6)
                        }
                    }
                    .padding(.horizontal, 18 * k)
                    }
                }
                .frame(width: size, height: size)
                .contentShape(Circle())
                // a tap on the lamp opens on the Mac the session it is showing
                .onTapGesture { if let it = l.item { store.reveal(it) } else { store.acknowledge() } }
            }
            if !l.who.isEmpty {
                Text(l.who)
                    .font(.system(size: 22 * hk, weight: .bold))
                    .foregroundColor(.deskFG)
                    .lineLimit(1).truncationMode(.tail)
            }
            if let usage = store.payload?.usage, !usage.isEmpty, !store.stale {
                Quota(usage: usage, now: store.now, scale: hk)
            }
        }
        .frame(maxHeight: .infinity)
    }
}

// MARK: - What is left of each agent's quota, side by side under the lamp

struct Quota: View {
    let usage: [DeskUsage]
    let now: Date
    let scale: CGFloat
    static let staleAfter: TimeInterval = 6 * 3600

    private func tone(_ u: DeskUsage) -> Tone? {
        u.left < 5 ? .error : u.left < 20 ? .wait : nil
    }

    /// when a low quota comes back: "14:00" today, "周四 8:00" later in the week
    private func resets(_ u: DeskUsage) -> String {
        guard u.resets_at > 0 else { return "" }
        let d = Date(timeIntervalSince1970: u.resets_at / 1000)
        let f = DateFormatter()
        f.locale = Locale(identifier: chinese ? "zh_CN" : "en_US")
        f.dateFormat = Calendar.current.isDate(d, inSameDayAs: now) ? "H:mm" : "EEE H:mm"
        return f.string(from: d)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 14 * scale) {
            ForEach(usage, id: \.name) { u in
                let t = tone(u)
                VStack(alignment: .leading, spacing: 3 * scale) {
                    Text(u.name)
                        .font(.system(size: 12 * scale))
                        .foregroundColor(.deskDim)
                    Text("\(u.left)%")
                        .font(.system(size: 18 * scale, weight: .semibold, design: .rounded).monospacedDigit())
                        .foregroundColor(t?.text ?? .deskFG)
                    GeometryReader { g in
                        ZStack(alignment: .leading) {
                            Capsule().fill(Color(white: 0.16))
                            Capsule().fill(t?.a(0.9) ?? Color.deskDim)
                                .frame(width: g.size.width * CGFloat(u.left) / 100)
                        }
                    }
                    .frame(height: 4)
                    if t != nil, !resets(u).isEmpty {
                        Text(resets(u))
                            .font(.system(size: 11 * scale).monospacedDigit())
                            .foregroundColor(.deskDim)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                // numbers nobody has refreshed for hours are shown, but faded
                .opacity(now.timeIntervalSince1970 - u.at / 1000 > Self.staleAfter ? 0.4 : 1)
            }
        }
        .lineLimit(1)
    }
}

// MARK: - Six keys, two across: one per session, lit in its state's colour

struct Keys: View {
    @ObservedObject var store: DeskStore
    let height: CGFloat
    static let count = 6

    var body: some View {
        let gap: CGFloat = 12
        let keyH = (height - gap * 2) / 3
        let items = store.items
        var seen: [String: Int] = [:]
        items.prefix(Self.count).forEach { seen[$0.project, default: 0] += 1 }
        return VStack(spacing: gap) {
            ForEach(0..<3, id: \.self) { row in
                HStack(spacing: gap) {
                    ForEach(0..<2, id: \.self) { col in
                        let i = row * 2 + col
                        let it: DeskItem? = i < items.count ? items[i] : nil
                        Key(item: it, store: store, height: keyH,
                            twin: it.map { (seen[$0.project] ?? 0) > 1 } ?? false)
                    }
                }
                .frame(height: keyH)
            }
        }
    }
}

struct Key: View {
    let item: DeskItem?
    @ObservedObject var store: DeskStore
    let height: CGFloat
    let twin: Bool          // two sessions of one project: say which with the title

    private var status: String {
        guard let it = item else { return "" }
        if let n = store.notes[it.sid] { return n }
        let s = store.age(it)
        switch it.shown {
        case "wait": return tr("等你 " + Ages.short(s), "waiting " + Ages.short(s))
        case "run": return tr("运行中", "running")
        case "bg": return tr("等后台", "background")
        case "quiet": return tr("没动静", "quiet")
        case "done": return s < 60 ? tr("刚完成", "just done") : tr("完成 · " + Ages.short(s) + "前", "done " + Ages.short(s) + " ago")
        case "idle": return tr("空闲", "idle")
        case "limit": return tr("额度用完", "out of quota")
        default: return tr("出错", "error")
        }
    }

    private var note: String {
        guard let it = item else { return "" }
        if twin { return it.title }
        if it.state == "error", !it.err.isEmpty { return it.err }
        return ""
    }

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 18, style: .continuous)
        let st = item?.shown ?? "off"
        let tone = Tone.of(st)
        let k = min(1, height / 120)
        let breath = Breath.of(st)
        return Pulse(breath: breath) { p in
            ZStack(alignment: .leading) {
                // the keycap: a little lighter on top, with a shadow below, lit or not
                shape.fill(LinearGradient(colors: [Color(white: item == nil ? 0.045 : 0.075),
                                                   Color(white: item == nil ? 0.025 : 0.045)],
                                          startPoint: .top, endPoint: .bottom))
                    .shadow(color: .black.opacity(0.7), radius: 3, x: 0, y: 3)
                if item != nil {
                    // needs you: swells between 70 and 100 %; running: breathes, slower, between 40 and 100 %
                    let glow = st == "idle" ? 0.35
                        : breath == .needsYou ? 0.7 + 0.3 * p
                        : breath == .running ? 0.4 + 0.6 * p : 1
                    shape.fill(EllipticalGradient(stops: [.init(color: tone.a(0.26), location: 0),
                                                          .init(color: tone.a(0.12), location: 0.6),
                                                          .init(color: tone.a(0.03), location: 1)],
                                                  center: .center, startRadiusFraction: 0, endRadiusFraction: 0.62))
                        .opacity(glow)
                    shape.stroke(tone.a(0.14), lineWidth: 18).blur(radius: 10).clipShape(shape).opacity(glow)
                    VStack(alignment: .leading, spacing: 4 * k) {
                        Text(item?.project ?? "")
                            .font(.system(size: 25 * k, weight: .bold))
                            .foregroundColor(st == "idle" ? Color(white: 0.55) : .deskFG)
                            .lineLimit(1).minimumScaleFactor(0.65)
                        if !note.isEmpty {
                            Text(note)
                                .font(.system(size: 12 * k))
                                .foregroundColor(.deskDim)
                                .lineLimit(1)
                        }
                        Text(status)
                            .font(.system(size: 19 * k, weight: st == "idle" ? .medium : .bold).monospacedDigit())
                            .foregroundColor(st == "idle" ? .deskDim : tone.text)
                            .lineLimit(1).minimumScaleFactor(0.7)
                    }
                    .padding(.horizontal, 14 * k)
                }
                shape.strokeBorder(item == nil ? Color(white: 0.085)
                                   : tone.a(st == "wait" ? 0.6 : breath == .running ? 0.22 + 0.22 * p : 0.38), lineWidth: 1)
                // the cap's edge: light catches the top, the bottom falls into shade
                shape.strokeBorder(LinearGradient(stops: [.init(color: .white.opacity(0.09), location: 0),
                                                          .init(color: .clear, location: 0.3),
                                                          .init(color: .clear, location: 0.7),
                                                          .init(color: .black.opacity(0.35), location: 1)],
                                                  startPoint: .top, endPoint: .bottom), lineWidth: 1.5)
            }
            .contentShape(shape)
            .onTapGesture { if let it = item { store.reveal(it) } }
        }
    }
}
