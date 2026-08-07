//
//  AudioPlayer.swift — plays the backend's raw PCM narration.
//
//  /audio/{i}.pcm hands back headerless PCM because the ESP32 writes those
//  bytes straight to I2S and has no decoder. AVAudioPlayer does want a
//  container, so we prepend a 44-byte WAV header rather than adding a second
//  audio endpoint to the backend for the sake of one client.
//

import AVFoundation
import Foundation

@MainActor
final class AudioPlayer: NSObject, ObservableObject {

    /// Which article is currently speaking, if any. The Listen button reads
    /// this to say what it will do next, the same way the device's does.
    @Published private(set) var playingIndex: Int?
    @Published private(set) var isLoading = false
    @Published private(set) var failed = false

    private var player: AVAudioPlayer?

    var isPlaying: Bool { playingIndex != nil }

    func toggle(index: Int, using client: NewsClient) {
        if playingIndex == index || isLoading {
            stop()
        } else {
            play(index: index, using: client)
        }
    }

    func play(index: Int, using client: NewsClient) {
        stop()
        isLoading = true
        failed = false

        Task {
            do {
                let (pcm, format) = try await client.fetchAudio(index: index)
                // A stop() while synthesis was in flight must not be undone
                // by the response landing afterwards.
                guard isLoading else { return }

                let player = try AVAudioPlayer(data: Self.wrapInWAV(pcm, format))
                player.delegate = self
                player.play()

                self.player = player
                self.playingIndex = index
                self.isLoading = false
            } catch {
                self.isLoading = false
                self.failed = true
                self.playingIndex = nil
            }
        }
    }

    func stop() {
        player?.stop()
        player = nil
        playingIndex = nil
        isLoading = false
    }

    // MARK: - WAV wrapping

    /// Minimal 44-byte canonical WAV header around raw little-endian PCM.
    private static func wrapInWAV(_ pcm: Data, _ format: PCMFormat) -> Data {
        let sampleRate = format.sampleRate
        let bits       = format.bits
        let channels   = format.channels

        let byteRate   = sampleRate * UInt32(channels) * UInt32(bits / 8)
        let blockAlign = channels * (bits / 8)

        var out = Data(capacity: 44 + pcm.count)
        func ascii(_ s: String)  { out.append(contentsOf: Array(s.utf8)) }
        func u32(_ v: UInt32)    { withUnsafeBytes(of: v.littleEndian) { out.append(contentsOf: $0) } }
        func u16(_ v: UInt16)    { withUnsafeBytes(of: v.littleEndian) { out.append(contentsOf: $0) } }

        ascii("RIFF")
        u32(UInt32(36 + pcm.count))     // chunk size: everything after this field
        ascii("WAVE")

        ascii("fmt ")
        u32(16)                         // PCM fmt chunk length
        u16(1)                          // format 1 = uncompressed PCM
        u16(channels)
        u32(sampleRate)
        u32(byteRate)
        u16(blockAlign)
        u16(bits)

        ascii("data")
        u32(UInt32(pcm.count))
        out.append(pcm)

        return out
    }
}

extension AudioPlayer: AVAudioPlayerDelegate {
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.playingIndex = nil
            self.player = nil
        }
    }
}
