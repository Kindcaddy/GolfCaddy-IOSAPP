import AVFoundation
import Foundation
import MediaPlayer
import UIKit

extension Notification.Name {
    static let kindCaddyHeadsetMicToggle = Notification.Name("KindCaddy.headsetMicToggle")
}

/// Owns the MPRemoteCommandCenter lifecycle and audio session for the round.
/// Uses AVQueuePlayer + AVPlayerLooper with a silent WAV on a `.playback` session to claim the
/// "now playing" slot. Switches to `.playAndRecord` only while recording, then back to `.playback`.
final class HeadsetRemoteCoordinator: NSObject, ObservableObject {
    private var lastEventTime: CFAbsoluteTime = 0
    private let debounceInterval: CFTimeInterval = 0.25
    private var queuePlayer: AVQueuePlayer?
    private var playerLooper: AVPlayerLooper?
    private var silenceURL: URL?

    func activate() {
        UIApplication.shared.beginReceivingRemoteControlEvents()

        let cc = MPRemoteCommandCenter.shared()

        cc.togglePlayPauseCommand.isEnabled = true
        cc.togglePlayPauseCommand.removeTarget(nil)
        cc.togglePlayPauseCommand.addTarget { [weak self] _ in self?.onRemote() ?? .noActionableNowPlayingItem }

        cc.playCommand.isEnabled = true
        cc.playCommand.removeTarget(nil)
        cc.playCommand.addTarget { [weak self] _ in self?.onRemote() ?? .noActionableNowPlayingItem }

        cc.pauseCommand.isEnabled = true
        cc.pauseCommand.removeTarget(nil)
        cc.pauseCommand.addTarget { [weak self] _ in self?.onRemote() ?? .noActionableNowPlayingItem }

        cc.nextTrackCommand.isEnabled = false
        cc.previousTrackCommand.isEnabled = false

        setupPlaybackSessionAndPlay()
        print("[HeadsetRemote] activated")
    }

    func deactivate() {
        queuePlayer?.pause()
        queuePlayer = nil
        playerLooper = nil

        let cc = MPRemoteCommandCenter.shared()
        for cmd in [cc.togglePlayPauseCommand, cc.playCommand, cc.pauseCommand] {
            cmd.removeTarget(nil)
            cmd.isEnabled = false
        }

        UIApplication.shared.endReceivingRemoteControlEvents()

        if let url = silenceURL { try? FileManager.default.removeItem(at: url) }
        silenceURL = nil

        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        print("[HeadsetRemote] deactivated")
    }

    /// Switch to `.playAndRecord` so the mic route becomes available.
    /// Call this BEFORE starting the audio engine, with a brief delay for the route to settle.
    @discardableResult
    func switchToRecordingSession() -> Bool {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playAndRecord, mode: .default,
                options: [.allowBluetooth, .defaultToSpeaker, .duckOthers]
            )
            try session.setActive(true)
            print("[HeadsetRemote] switched to .playAndRecord")
            return true
        } catch {
            let nsError = error as NSError
            // 560557684 ("!int") can occur from lock-screen/background interruption rules.
            // Retry with mixWithOthers so iOS does not require interruption rights.
            if nsError.domain == NSOSStatusErrorDomain, nsError.code == 560557684 {
                do {
                    try session.setCategory(
                        .playAndRecord, mode: .default,
                        options: [.allowBluetooth, .defaultToSpeaker, .mixWithOthers]
                    )
                    try session.setActive(true)
                    print("[HeadsetRemote] switched to .playAndRecord (mixWithOthers fallback)")
                    return true
                } catch {
                    print("[HeadsetRemote] .playAndRecord fallback failed: \(error)")
                }
            } else {
                print("[HeadsetRemote] .playAndRecord switch failed: \(error)")
            }
            return false
        }
    }

    /// Switch back to `.playback` so remote commands resume routing to us.
    func switchToPlaybackSession() {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            queuePlayer?.play()
            print("[HeadsetRemote] switched back to .playback")
        } catch {
            let nsError = error as NSError
            // 560557684 ("!int") == cannotInterruptOthers in background/locked states.
            // Retry with a mixing playback category so iOS does not require interruption rights.
            if nsError.domain == NSOSStatusErrorDomain, nsError.code == 560557684 {
                do {
                    try session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
                    try session.setActive(true, options: .notifyOthersOnDeactivation)
                    queuePlayer?.play()
                    print("[HeadsetRemote] switched to .playback (mixWithOthers fallback)")
                    return
                } catch {
                    print("[HeadsetRemote] .playback fallback failed: \(error)")
                }
            } else {
                print("[HeadsetRemote] .playback switch failed: \(error)")
            }
        }
    }

    // MARK: - Remote event handling

    private func onRemote() -> MPRemoteCommandHandlerStatus {
        print("[HeadsetRemote] *** remote event received ***")
        let now = CFAbsoluteTimeGetCurrent()
        if now - lastEventTime < debounceInterval {
            print("[HeadsetRemote] debounced")
            return .success
        }
        lastEventTime = now
        DispatchQueue.main.async {
            NotificationCenter.default.post(name: .kindCaddyHeadsetMicToggle, object: nil)
        }
        return .success
    }

    // MARK: - Silent player on .playback session

    private func setupPlaybackSessionAndPlay() {
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true)
            print("[HeadsetRemote] audio session: .playback / .default — active")
        } catch {
            print("[HeadsetRemote] audio session setup failed: \(error)")
            return
        }

        let wavData = Self.silentWAV()
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("kindcaddy_silence.wav")
        do {
            try wavData.write(to: url)
        } catch {
            print("[HeadsetRemote] could not write silence file: \(error)")
            return
        }
        silenceURL = url

        let templateItem = AVPlayerItem(url: url)
        let player = AVQueuePlayer(items: [AVPlayerItem(url: url)])
        player.volume = 1
        playerLooper = AVPlayerLooper(player: player, templateItem: templateItem)
        player.play()
        queuePlayer = player

        print("[HeadsetRemote] silent AVPlayer started, looping")
    }

    /// Minimal 16-bit mono WAV — 200 ms of silence at 8 kHz.
    private static func silentWAV() -> Data {
        let sampleRate: UInt32 = 8000
        let numSamples: UInt32 = 1600
        let bitsPerSample: UInt16 = 16
        let channels: UInt16 = 1
        let dataSize = numSamples * UInt32(channels) * UInt32(bitsPerSample / 8)
        let fileSize = 36 + dataSize

        var d = Data()
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }

        d.append(contentsOf: [0x52, 0x49, 0x46, 0x46])
        u32(fileSize)
        d.append(contentsOf: [0x57, 0x41, 0x56, 0x45])
        d.append(contentsOf: [0x66, 0x6D, 0x74, 0x20])
        u32(16)
        u16(1)
        u16(channels)
        u32(sampleRate)
        u32(sampleRate * UInt32(channels) * UInt32(bitsPerSample / 8))
        u16(channels * (bitsPerSample / 8))
        u16(bitsPerSample)
        d.append(contentsOf: [0x64, 0x61, 0x74, 0x61])
        u32(dataSize)
        d.append(Data(count: Int(dataSize)))

        return d
    }
}
