import AppKit
import WebKit

@MainActor
final class MirrorApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler, WKNavigationDelegate {
    private var window: NSWindow!
    private var web: WKWebView!
    private var overlay: NSStackView!
    private let message = NSTextField(wrappingLabelWithString: "Connecting to your iPhone…")
    private var retry: NSButton!
    private var command: Process?
    private var quitting = false
    private var connected = false
    private var lastLandscape: Bool?
    private let root: URL
    private let preview = URL(string: CommandLine.arguments.contains("--benchmark")
                              ? "http://127.0.0.1:15801/?benchmark=1"
                              : "http://127.0.0.1:15801/")!

    override init() {
        // This local app is built for this checkout; moving the bundle is supported.
        let path = Bundle.main.object(forInfoDictionaryKey: "BridgeRoot") as? String ?? ""
        root = URL(fileURLWithPath: path, isDirectory: true)
        super.init()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        makeMenu()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 350, height: 758),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "iPhone"
        window.subtitle = "Connecting…"
        window.backgroundColor = .black
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.collectionBehavior = [.fullScreenPrimary]
        window.center()

        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(self, name: "mirror")
        web = WKWebView(frame: .zero, configuration: configuration)
        web.navigationDelegate = self
        web.translatesAutoresizingMaskIntoConstraints = false
        web.underPageBackgroundColor = .black
        let content = NSView()
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.black.cgColor
        content.addSubview(web)
        window.contentView = content
        NSLayoutConstraint.activate([
            web.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            web.topAnchor.constraint(equalTo: content.topAnchor),
            web.bottomAnchor.constraint(equalTo: content.bottomAnchor)
        ])
        message.textColor = .white
        message.alignment = .center
        message.font = .systemFont(ofSize: 14)
        retry = NSButton(title: "Reconnect", target: self, action: #selector(reconnect))
        retry.bezelStyle = .rounded
        retry.isHidden = true
        overlay = NSStackView(views: [message, retry])
        overlay.orientation = .vertical
        overlay.spacing = 18
        overlay.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(overlay)
        NSLayoutConstraint.activate([
            overlay.centerXAnchor.constraint(equalTo: content.centerXAnchor),
            overlay.centerYAnchor.constraint(equalTo: content.centerYAnchor),
            overlay.widthAnchor.constraint(lessThanOrEqualTo: content.widthAnchor, constant: -48)
        ])
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        connect()
    }

    private func makeMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About iPhoneBridge", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit iPhoneBridge", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)
        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Reconnect", action: #selector(reconnect), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        viewMenu.items.last?.keyEquivalentModifierMask = [.control, .command]
        viewItem.submenu = viewMenu
        menu.addItem(viewItem)
        NSApp.mainMenu = menu
    }

    @objc private func showAbout() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "iPhoneBridge",
            .applicationVersion: "0.1.0", .credits: NSAttributedString(string: "Live iPhone mirroring over USB.\nMIT bridge; TrollVNC and noVNC retain their own licenses.")])
    }

    private func connect() {
        guard command == nil, !quitting else { return }
        showMessage("Connecting to your iPhone…", canRetry: false)
        runBridge(["start"]) { [weak self] success, detail in
            guard let self, !self.quitting else { return }
            if success {
                self.web.load(URLRequest(url: self.preview))
            } else {
                self.showMessage("Couldn’t connect. Check the USB cable and that the iPhone is awake.\n\n" + detail, canRetry: true)
            }
        }
    }

    @objc private func reconnect() {
        guard command == nil, !quitting else { return }
        connected = false
        showMessage("Reconnecting…", canRetry: false)
        web.loadHTMLString("<body style='background:black'></body>", baseURL: nil)
        runBridge(["stop"]) { [weak self] success, detail in
            guard let self, !self.quitting else { return }
            if success { self.connect() }
            else { self.showMessage(detail, canRetry: true) }
        }
    }

    private func showMessage(_ text: String, canRetry: Bool) {
        message.stringValue = text
        retry.isHidden = !canRetry
        overlay.isHidden = false
        web.isHidden = true
        window.subtitle = canRetry ? "Disconnected" : "Connecting…"
    }

    private func runBridge(_ arguments: [String], completion: @escaping @MainActor (Bool, String) -> Void) {
        let process = Process()
        process.executableURL = root.appendingPathComponent("bridge")
        process.arguments = arguments
        process.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        process.environment = environment
        let logURL = root.appendingPathComponent("work/app-command.log")
        do {
            try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
            let log = try FileHandle(forWritingTo: logURL)
            process.standardOutput = log
            process.standardError = log
            process.standardInput = FileHandle.nullDevice
            process.terminationHandler = { [weak self] finished in
                try? log.close()
                let output = (try? String(contentsOf: logURL, encoding: .utf8)) ?? "See work/app-command.log."
                Task { @MainActor [weak self] in
                    self?.command = nil
                    completion(finished.terminationStatus == 0, output.trimmingCharacters(in: .whitespacesAndNewlines))
                }
            }
            command = process
            try process.run()
        } catch {
            command = nil
            completion(false, error.localizedDescription)
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == 15801,
              let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        if type == "resize", let width = body["width"] as? Double, let height = body["height"] as? Double,
           width > 0, height > 0, width <= 16384, height <= 16384 {
            resizePhone(width: width, height: height)
        } else if type == "status" {
            connected = body["connected"] as? Bool ?? false
            window.subtitle = connected ? "USB" : "Reconnecting…"
            overlay.isHidden = true
            web.isHidden = false
            if connected { window.makeFirstResponder(web) }
        }
    }

    private func resizePhone(width: Double, height: Double) {
        let landscape = width > height
        window.contentAspectRatio = NSSize(width: width, height: height)
        window.contentMinSize = landscape ? NSSize(width: 400, height: 180) : NSSize(width: 240, height: 480)
        guard lastLandscape != landscape, !window.styleMask.contains(.fullScreen) else { return }
        lastLandscape = landscape
        let screen = (window.screen ?? NSScreen.main)?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1200, height: 900)
        let scale = min(landscape ? 850 / width : 380 / width, (screen.height - 100) / height, (screen.width - 80) / width)
        let contentSize = NSSize(width: width * scale, height: height * scale)
        let oldFrame = window.frame
        let frameSize = window.frameRect(forContentRect: NSRect(origin: .zero, size: contentSize)).size
        let origin = NSPoint(x: max(screen.minX, min(oldFrame.midX - frameSize.width / 2, screen.maxX - frameSize.width)),
                             y: max(screen.minY, min(oldFrame.maxY - frameSize.height, screen.maxY - frameSize.height)))
        window.setFrame(NSRect(origin: origin, size: frameSize), display: true, animate: true)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        let url = navigationAction.request.url
        decisionHandler((url?.host == "127.0.0.1" && url?.port == 15801) || url?.absoluteString == "about:blank" ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        showMessage("The preview couldn’t load. Reconnect to try again.", canRetry: true)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        showMessage("The preview stopped. Reconnect to continue.", canRetry: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return .terminateLater }
        quitting = true
        // Finish an in-progress start/stop before issuing the final owned cleanup.
        Task { @MainActor in
            while command != nil { try? await Task.sleep(for: .milliseconds(100)) }
            runBridge(["stop"]) { _, _ in NSApp.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
}

@main
struct Main {
    @MainActor static func main() {
        let app = NSApplication.shared
        let delegate = MirrorApp()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { app.run() }
    }
}
