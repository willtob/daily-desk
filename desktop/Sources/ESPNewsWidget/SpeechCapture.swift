//
//  SpeechCapture.swift — the microphone half of explaining out loud.
//
//  The Learn tab's explain screen is a recorder, not a text box, and this is
//  the thing behind the button. It owns an AVAudioEngine tap, an on-device
//  SpeechAnalyzer, and the two numbers the recorder pill draws: an input level
//  and a duration. Nothing here talks to the network and nothing here leaves
//  the machine — SpeechTranscriber runs against a locally installed model, so
//  four minutes of thinking out loud is not four minutes uploaded somewhere.
//
//  **Why this is two types.** Everything in the Speech.framework API this uses
//  — SpeechAnalyzer, SpeechTranscriber, AnalysisContext, AssetInventory — is
//  `@available(macOS 26.0, *)`, and the app's floor is macOS 14 (Package.swift,
//  and LSMinimumSystemVersion in Info.plist). A type carrying that availability
//  cannot be a stored property of anything that doesn't, which rules out the
//  obvious `@StateObject private var capture = SpeechCapture()` in the view.
//  So the observable object the view holds — `SpeechCapture` — is available
//  everywhere and holds `any SpeechEngine`; the concrete engine that imports
//  the new API is gated. On macOS 14 the engine is simply never constructed and
//  `SpeechCapture.isSupported` is false, which is what LearnView reads to decide
//  whether to offer the recorder at all.
//
//  **What is published and what is not.** The transcript is deliberately *not*
//  streamed into the UI. Volatile results arrive several times a second, and
//  putting them on screen turns "explain the topic" into "watch your own words
//  appear", which is the exact failure the whole redesign exists to avoid. They
//  are also the reason `LearnStore.explanation` must not be written on every
//  result: that property has a `didSet` that writes the crash-recovery draft to
//  UserDefaults (see LearnStore's Crash recovery section), and a volatile feed
//  would turn that into a few hundred writes a minute. Only *finalized* results
//  are handed over, via `onCommit`, and finalized results arrive at roughly the
//  cadence of a sentence.
//
//  **Measured on this machine**, feeding 198 s of speech through the analyzer at
//  real-time pace (scratchpad/spike): en_US is in `installedLocales` out of the
//  box, but `AssetInventory.status` starts at `.supported` rather than
//  `.installed` because the locale has not been *reserved* by this app;
//  `assetInstallationRequest(supporting:)` + `downloadAndInstall()` fixes that
//  in well under a second when the model is already on disk, and the
//  reservation persists across launches. `bestAvailableAudioFormat` is 16 kHz
//  mono Int16. That is why `prepare()` exists and why it is cheap to call every
//  time rather than being cached.
//

// @preconcurrency for AVFoundation only, and for one warning: AVAudioConverter's
// input block is `@Sendable` while AVAudioPCMBuffer is not, so handing a buffer
// to the converter that was just handed to us cannot be expressed without it.
// The buffer does not escape `resample` and the converter is called
// synchronously, so the guarantee the annotation is asking for does hold — it
// just cannot be written down in Swift 5's terms.
@preconcurrency import AVFoundation
import Combine
import Foundation
import Speech

// MARK: - The engine, behind a version-free door

/// What `SpeechCapture` needs from a transcription engine, expressed without
/// naming a single macOS 26 type — which is the whole point of it. See the
/// header: this protocol is the seam that lets the view hold a plain
/// `@StateObject` on a macOS 14 deployment target.
@MainActor
protocol SpeechEngine: AnyObject {
    /// Downloads or reserves whatever the transcriber needs. Fast when the
    /// model is already installed; slow exactly once on a fresh machine.
    func prepare() async throws

    /// Starts the audio tap and the analyzer. Throws rather than reporting
    /// through a callback because everything that can go wrong here goes wrong
    /// before the first sample.
    func start() async throws

    /// Stops the tap, drains the analyzer, and delivers any trailing text
    /// through `onCommit` before returning.
    func stop() async

    /// Peak level, 0...1, called from the audio thread's hop to main at
    /// whatever rate the tap fires.
    var onLevel: ((Double) -> Void)? { get set }

    /// One finalized span of transcript. Never volatile text — see the header.
    var onCommit: ((String) -> Void)? { get set }

    /// The analyzer's result sequence failed mid-session.
    var onFailure: ((Error) -> Void)? { get set }
}

// MARK: - The observable the view holds

@MainActor
final class SpeechCapture: ObservableObject {

    enum State: Equatable {
        case idle
        /// First run only, in practice: reserving the locale or downloading a
        /// model. Given a state of its own because on a machine that has never
        /// used it this is a multi-minute download, and a dead button is the
        /// worst possible way to say so.
        case preparing
        case recording
        /// Between the button press and the analyzer having drained. Short,
        /// but it is when the last sentence arrives, so submitting during it
        /// would grade an explanation with its ending cut off.
        case finishing
        case failed(String)
    }

    @Published private(set) var state: State = .idle

    /// How long the microphone has actually been open, summed across pauses.
    /// This is what the review screen reports, so it has to exclude the time
    /// spent stopped — otherwise pausing to think inflates it.
    @Published private(set) var duration: TimeInterval = 0

    /// The waveform, newest last, each entry 0...1. A fixed-length rolling
    /// window rather than the whole session: the pill shows a few seconds and
    /// keeping the rest would be a slowly growing array nobody reads.
    @Published private(set) var levels: [Double] = []

    /// True once any speech has been committed this session. LearnStore reads
    /// it through the view to decide whether the graded text arrived by voice.
    @Published private(set) var didCapture = false

    /// Called with each finalized span. LearnView wires this to the store.
    var onCommit: ((String) -> Void)?

    /// "4:12", counting up. The recorder's caption while it is running.
    var durationText: String {
        let whole = Int(max(duration, 0))
        return String(format: "%d:%02d", whole / 60, whole % 60)
    }

    // MARK: Configuration

    /// Kept long enough to fill the widest recorder pill this panel will ever
    /// show, with room to spare. `RecorderPill` takes the suffix it needs.
    static let windowSize = 96

    /// The waveform advances on a timer rather than on the audio tap, so the
    /// scroll speed is a property of the design instead of a property of
    /// whichever microphone is plugged in — a tap's buffer duration is set by
    /// the hardware and differs between the built-in mic and AirPods, and the
    /// pill visibly scrolling at a different rate on each would be a bug that
    /// only appears when someone changes input device.
    private static let frameRate: TimeInterval = 1.0 / 12

    /// Peak-hold ballistics, in level units per frame. Speech is spiky; a bare
    /// per-frame peak reads as noise, and a slow average reads as a flat line.
    /// Instant attack with a decay tuned so a syllable's tail is still visible
    /// two or three bars later is what makes it look like a waveform.
    private static let decay = 0.16

    /// True when this machine can transcribe at all. False below macOS 26,
    /// where LearnView falls back to the typed flow it has always had.
    ///
    /// `nonisolated` for the same reason LearnStore's `sessionDuration` is: it
    /// is read from a default argument, and a default argument is evaluated
    /// outside the actor it is a default for.
    nonisolated static var isSupported: Bool {
        if #available(macOS 26.0, *) { return SpeechTranscriber.isAvailable }
        return false
    }

    // MARK: Private state

    private var engine: (any SpeechEngine)?
    private var frameTimer: Task<Void, Never>?
    private var startedAt: Date?
    private var accumulated: TimeInterval = 0
    private var peak = 0.0
    private var held = 0.0

    /// Terms to bias the recogniser toward — see `AnalysisContext`. In practice
    /// the drawn topic's own name, because "the KV cache" and "LoRA" are
    /// exactly the words a general model gets wrong and exactly the words the
    /// grader is looking for.
    var contextualStrings: [String] = []

    // MARK: - Lifecycle

    /// Toggle. The circular button is the only control, so this is the only
    /// entry point the view needs.
    func toggle() async {
        switch state {
        case .idle, .failed: await begin()
        case .recording:     await end()
        case .preparing, .finishing: break
        }
    }

    private func begin() async {
        guard #available(macOS 26.0, *), Self.isSupported else {
            state = .failed("Speech needs macOS 26.")
            return
        }

        state = .preparing

        let engine = LiveSpeechEngine(contextualStrings: contextualStrings)
        engine.onLevel   = { [weak self] level in self?.peak = max(self?.peak ?? 0, level) }
        engine.onFailure = { [weak self] error in
            guard let self else { return }
            self.stopClock()
            self.state = .failed(Self.describe(error))
        }
        engine.onCommit  = { [weak self] text in
            guard let self, !text.isEmpty else { return }
            self.didCapture = true
            self.onCommit?(text)
        }

        do {
            try await engine.prepare()
            try await engine.start()
        } catch {
            state = .failed(Self.describe(error))
            return
        }

        self.engine = engine
        startedAt = Date()
        startClock()
        state = .recording
    }

    private func end() async {
        state = .finishing
        stopClock()
        await engine?.stop()
        engine = nil
        state = .idle
    }

    /// Everything back to zero, for Discard and for a new session. Safe to
    /// call from any state; the view calls it without knowing which one it is.
    func reset() {
        Task { [weak self] in
            guard let self else { return }
            if self.state == .recording || self.state == .finishing {
                await self.engine?.stop()
            }
            self.engine = nil
            self.stopClock()
            self.accumulated = 0
            self.duration = 0
            self.levels = []
            self.peak = 0
            self.held = 0
            self.didCapture = false
            self.state = .idle
        }
    }

    // MARK: - The display clock

    private func startClock() {
        stopClock()
        frameTimer = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(Self.frameRate))
                guard let self else { return }
                self.frame()
            }
        }
    }

    private func stopClock() {
        frameTimer?.cancel()
        frameTimer = nil
        if let startedAt {
            accumulated += Date().timeIntervalSince(startedAt)
            self.startedAt = nil
        }
        duration = accumulated
        // Decayed rather than emptied: a waveform that vanishes the instant the
        // button is pressed looks like the recording was thrown away.
        peak = 0
    }

    private func frame() {
        held = max(peak, held - Self.decay)
        peak = 0

        levels.append(min(max(held, 0), 1))
        if levels.count > Self.windowSize {
            levels.removeFirst(levels.count - Self.windowSize)
        }

        if let startedAt {
            duration = accumulated + Date().timeIntervalSince(startedAt)
        }
    }

    // MARK: - Errors the user can act on

    /// The two failures worth distinguishing are "you said no to the
    /// microphone" and "everything else", because only the first has something
    /// the user can go and do about it. TCC denial surfaces as a plain `false`
    /// from `requestAccess` rather than as an error, so `CaptureError` carries
    /// it and this turns it into the one sentence that helps.
    private static func describe(_ error: Error) -> String {
        if let error = error as? CaptureError { return error.message }
        return error.localizedDescription
    }
}

enum CaptureError: Error {
    case microphoneDenied
    case noAudioFormat
    case unsupported

    var message: String {
        switch self {
        case .microphoneDenied:
            return "Microphone access is off — System Settings › Privacy › Microphone."
        case .noAudioFormat:
            return "No compatible audio format for transcription."
        case .unsupported:
            return "Speech transcription is unavailable on this Mac."
        }
    }
}

// MARK: - The real engine

/// AVAudioEngine on one end, SpeechAnalyzer on the other, an AsyncStream of
/// `AnalyzerInput` in between.
///
/// The shape is the one Apple's own sample uses and the one the spike measured:
/// a tap on the input node, each buffer resampled to the analyzer's preferred
/// format, yielded into a stream the analyzer consumes on its own task, with
/// results read from `transcriber.results` on a third. Three concurrent things
/// for what sounds like one job, and all three are necessary — the tap cannot
/// block, the analyzer cannot be driven synchronously, and the results sequence
/// outlives the audio by however long the final pass takes.
@available(macOS 26.0, *)
@MainActor
final class LiveSpeechEngine: SpeechEngine {

    var onLevel:   ((Double) -> Void)?
    var onCommit:  ((String) -> Void)?
    var onFailure: ((Error) -> Void)?

    private let contextualStrings: [String]

    private let audio = AVAudioEngine()
    private var transcriber: SpeechTranscriber?
    private var analyzer: SpeechAnalyzer?
    private var continuation: AsyncStream<AnalyzerInput>.Continuation?
    private var results: Task<Void, Never>?
    private var converter: AVAudioConverter?
    private var tapped = false

    /// Speech sits well under full scale and a linear meter leaves the bars
    /// flat. These are the dB window the level is mapped across: -52 is quiet
    /// room tone on the built-in mic, -8 is someone speaking up close.
    /// Read from the audio thread, hence nonisolated.
    private nonisolated static let floorDB: Double = -52
    private nonisolated static let ceilDB:  Double = -8

    init(contextualStrings: [String]) {
        self.contextualStrings = contextualStrings
    }

    // MARK: Assets

    func prepare() async throws {
        guard SpeechTranscriber.isAvailable else { throw CaptureError.unsupported }

        let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale.current)
            ?? Locale(identifier: "en-US")

        // `.progressiveTranscription` is volatileResults + fastResults. The
        // volatile ones are thrown away here (see SpeechCapture's header) but
        // asking for them is still right: with the preset that omits them the
        // analyzer finalizes in much larger chunks, and a four-minute answer
        // then arrives as two or three enormous commits — which is fine for
        // grading and terrible for a Stop button that has to feel immediate.
        let transcriber = SpeechTranscriber(locale: locale, preset: .progressiveTranscription)
        self.transcriber = transcriber

        // Measured: this is `.supported` rather than `.installed` on a machine
        // that already has the en_US model on disk, because the locale has not
        // been reserved by *this app*. The request below is what reserves it,
        // and it returns in milliseconds in that case. Only a genuinely absent
        // model makes this slow, and then it is a real download.
        if await AssetInventory.status(forModules: [transcriber]) != .installed,
           let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            try await request.downloadAndInstall()
        }
    }

    // MARK: Running

    func start() async throws {
        guard let transcriber else { throw CaptureError.unsupported }

        // Asked for explicitly, before the input node is touched. Reading
        // `audio.inputNode` is itself what triggers the TCC prompt, and doing
        // it implicitly means the prompt appears with no idea which of the two
        // outcomes happened — `requestAccess` at least answers that.
        guard await AVCaptureDevice.requestAccess(for: .audio) else {
            throw CaptureError.microphoneDenied
        }

        guard let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
            compatibleWith: [transcriber]
        ) else { throw CaptureError.noAudioFormat }

        let analyzer = SpeechAnalyzer(modules: [transcriber])
        self.analyzer = analyzer

        // Bias the recogniser toward the topic's own vocabulary. This is the
        // cheapest fix available for the one transcription error that actually
        // costs marks: a general model hearing "KV cache" as "TV cash" turns a
        // correct explanation into one that never named the mechanism, and the
        // grader has no way to tell that apart from not knowing it.
        if !contextualStrings.isEmpty {
            let context = AnalysisContext()
            context.contextualStrings = [.general: contextualStrings]
            try? await analyzer.setContext(context)
        }

        // Read results before any audio is fed in. Starting the analyzer first
        // and attaching afterwards is a race that loses the opening words on a
        // fast machine and never loses them on a slow one, which is the worst
        // kind.
        results = Task { [weak self] in
            do {
                for try await result in transcriber.results {
                    // Volatile results are dropped on the floor here. That is
                    // the design, not an omission — see SpeechCapture's header.
                    guard result.isFinal else { continue }
                    let text = String(result.text.characters)
                    await MainActor.run { self?.onCommit?(text) }
                }
            } catch {
                await MainActor.run { self?.onFailure?(error) }
            }
        }

        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        self.continuation = continuation

        let input = audio.inputNode
        let inputFormat = input.outputFormat(forBus: 0)

        // A zero sample rate means the input device is not actually there —
        // every microphone unplugged, or the node queried before CoreAudio has
        // resolved one. `installTap` with that format crashes rather than
        // failing, so it is checked here where it can still be an error message.
        guard inputFormat.sampleRate > 0,
              let converter = AVAudioConverter(from: inputFormat, to: analyzerFormat)
        else { throw CaptureError.noAudioFormat }
        self.converter = converter

        // 4096 frames is about 85 ms at 48 kHz — small enough that stopping
        // feels immediate, large enough that the resampler is not being woken
        // up several hundred times a second for a widget.
        //
        // The converter is handed to the closure rather than read back off
        // `self`. It has to be, and the reason is worth writing down: this
        // block runs on an audio render thread, `self` is @MainActor, and every
        // way of reaching a main-actor property from here is either a data race
        // or `MainActor.assumeIsolated`, which does not check-and-fall-back —
        // it traps. Capturing the value is the only version that is correct at
        // runtime as well as at compile time.
        input.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) { [weak self] buffer, _ in
            let level = Self.level(of: buffer)
            // Yielding into an AsyncStream is safe from here; touching
            // @Published state is not, hence the hop.
            Task { @MainActor [weak self] in self?.onLevel?(level) }
            if let converted = Self.resample(buffer, with: converter) {
                continuation.yield(AnalyzerInput(buffer: converted))
            }
        }
        tapped = true

        audio.prepare()
        try audio.start()
        try await analyzer.start(inputSequence: stream)
    }

    func stop() async {
        if tapped {
            audio.inputNode.removeTap(onBus: 0)
            tapped = false
        }
        audio.stop()

        continuation?.finish()
        continuation = nil

        // Drains what is still in flight and delivers the trailing sentence.
        // Awaited rather than fired and forgotten: the caller's next act is
        // usually to show a word count, and a count taken before this returns
        // is short by however much of the last sentence had not landed.
        try? await analyzer?.finalizeAndFinishThroughEndOfInput()
        _ = await results?.value

        results = nil
        analyzer = nil
        transcriber = nil
        converter = nil
    }

    // MARK: Audio plumbing

    /// One tap buffer, resampled to whatever the analyzer asked for.
    ///
    /// The converter is kept across calls rather than built per buffer. A fresh
    /// AVAudioConverter starts its resampler from silence, and rebuilding it
    /// every 85 ms puts a discontinuity at every buffer boundary — audible as a
    /// buzz, and worse than audible to the recogniser.
    private nonisolated static func resample(_ buffer: AVAudioPCMBuffer,
                                             with converter: AVAudioConverter) -> AVAudioPCMBuffer? {
        let ratio = converter.outputFormat.sampleRate / converter.inputFormat.sampleRate
        let capacity = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: converter.outputFormat,
                                         frameCapacity: capacity) else { return nil }

        var supplied = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, inputStatus in
            if supplied {
                // Not `.endOfStream`: that retires the converter, and this one
                // has to survive until the session does.
                inputStatus.pointee = .noDataNow
                return nil
            }
            supplied = true
            inputStatus.pointee = .haveData
            return buffer
        }

        guard error == nil, status != .error, out.frameLength > 0 else { return nil }
        return out
    }

    /// Peak magnitude of a buffer, mapped through dB onto 0...1.
    private nonisolated static func level(of buffer: AVAudioPCMBuffer) -> Double {
        let frames = Int(buffer.frameLength)
        guard frames > 0 else { return 0 }

        var peak: Float = 0
        if let channels = buffer.floatChannelData {
            let samples = channels[0]
            for i in 0..<frames { peak = max(peak, abs(samples[i])) }
        } else if let channels = buffer.int16ChannelData {
            let samples = channels[0]
            for i in 0..<frames { peak = max(peak, abs(Float(samples[i]) / 32768)) }
        }

        guard peak > 0 else { return 0 }
        let db = 20 * log10(Double(peak))
        return min(max((db - floorDB) / (ceilDB - floorDB), 0), 1)
    }
}
