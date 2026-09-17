import Cocoa
import WebKit

enum Program: String {
    case koenigssturz = "web"
    case reader = "reader"

    static var current: Program {
        let raw = Bundle.main.object(forInfoDictionaryKey: "KINGFALLProgram") as? String
        return Program(rawValue: raw ?? "web") ?? .koenigssturz
    }

    var title: String {
        switch self {
        case .koenigssturz: return "Königssturz"
        case .reader: return "VA Reader"
        }
    }

    var about: String {
        switch self {
        case .koenigssturz:
            return "Lokale Beweissicherung für Supabase, IONOS-Mail und Vereinsarchiv. Läuft nur auf diesem Rechner (localhost)."
        case .reader:
            return "Pakete aus Königssturz einspielen und Stammtische lokal lesen. Ohne cURL, nur auf diesem Rechner (localhost)."
        }
    }

    var pythonArgs: [String] {
        switch self {
        case .koenigssturz: return ["--web"]
        case .reader: return ["--reader"]
        }
    }

    var dataFolderName: String {
        switch self {
        case .koenigssturz: return "Königssturz"
        case .reader: return "VA Reader"
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    static let shared = AppDelegate()

    private let program = Program.current
    private var window: NSWindow!
    private var webView: WKWebView!
    private var loadingLabel: NSTextField!
    private var process: Process?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?
    private var stderrText = ""
    private var started = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenus()
        buildWindow()
        start()
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopServer()
    }

    private func buildMenus() {
        let mainMenu = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Über \(program.title)", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "\(program.title) beenden", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        mainMenu.addItem(appItem)

        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Bearbeiten")
        editMenu.addItem(withTitle: "Widerrufen", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Wiederholen", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(withTitle: "Ausschneiden", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Kopieren", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Einsetzen", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Alles auswählen", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)

        NSApp.mainMenu = mainMenu
    }

    @objc private func showAbout() {
        let alert = NSAlert()
        alert.messageText = program.title
        alert.informativeText = program.about
        alert.runModal()
    }

    private func buildWindow() {
        let rect = NSRect(x: 0, y: 0, width: 1180, height: 780)
        window = NSWindow(
            contentRect: rect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = program.title
        window.center()
        window.minSize = NSSize(width: 640, height: 400)
        window.contentView = NSView(frame: rect)
        window.makeKeyAndOrderFront(nil)

        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "allowFileAccessFromFileURLs")
        webView = WKWebView(frame: rect, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.isHidden = true
        window.contentView?.autoresizesSubviews = true
        window.contentView?.addSubview(webView)

        loadingLabel = NSTextField(labelWithString: "\(program.title) startet …")
        loadingLabel.font = NSFont.systemFont(ofSize: 15)
        loadingLabel.alignment = .center
        loadingLabel.isHidden = true
        loadingLabel.translatesAutoresizingMaskIntoConstraints = false
        window.contentView?.addSubview(loadingLabel)

        if let content = window.contentView {
            NSLayoutConstraint.activate([
                loadingLabel.centerXAnchor.constraint(equalTo: content.centerXAnchor),
                loadingLabel.centerYAnchor.constraint(equalTo: content.centerYAnchor),
                loadingLabel.leadingAnchor.constraint(greaterThanOrEqualTo: content.leadingAnchor, constant: 24),
                loadingLabel.trailingAnchor.constraint(lessThanOrEqualTo: content.trailingAnchor, constant: -24),
            ])
        }
    }

    private func start() {
        started = true
        window.title = program.title
        loadingLabel.stringValue = "\(program.title) startet …"
        loadingLabel.isHidden = false
        webView.isHidden = true

        guard let python = pythonExecutable() else {
            fail("python3 wurde nicht gefunden. Bitte die Xcode Command Line Tools oder Python 3 installieren.")
            return
        }
        guard let resources = Bundle.main.resourcePath else {
            fail("App-Bundle ohne Ressourcen.")
            return
        }

        let pythonRoot = (resources as NSString).appendingPathComponent("python")
        let pydeps = (resources as NSString).appendingPathComponent("pydeps")
        let script = (pythonRoot as NSString).appendingPathComponent("kingfall_macos.py")
        guard FileManager.default.isReadableFile(atPath: script) else {
            fail("Python-Dateien fehlen im App-Bundle.")
            return
        }

        let dataDir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(program.dataFolderName, isDirectory: true)
        try? FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = [script] + program.pythonArgs
        proc.currentDirectoryURL = dataDir

        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["KINGFALL_NO_BROWSER"] = "1"
        env["KINGFALL_CWD"] = dataDir.path
        env["PYTHONPATH"] = "\(pythonRoot):\(pydeps)"
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        proc.environment = env

        let out = Pipe()
        let err = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        stdoutPipe = out
        stderrPipe = err
        stderrText = ""

        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            self?.handleStdout(text)
        }
        err.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                self?.stderrText += text
            }
        }
        proc.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async {
                guard let self, self.process == finished, self.started else { return }
                let code = finished.terminationStatus
                // 15 = SIGTERM (App beendet), 9 = SIGKILL (z. B. Port-Kampf / Force-Quit)
                if code == 0 || code == 15 || code == 9 {
                    return
                }
                let detail = self.stderrText.trimmingCharacters(in: .whitespacesAndNewlines)
                let clipped = detail.count > 1200 ? String(detail.suffix(1200)) : detail
                self.fail("\(self.program.title) ist beendet (Status \(code)).\n\n\(clipped)")
            }
        }

        do {
            try proc.run()
            process = proc
        } catch {
            fail("Python ließ sich nicht starten: \(error.localizedDescription)")
        }
    }

    private func handleStdout(_ text: String) {
        DispatchQueue.main.async {
            for line in text.split(whereSeparator: \.isNewline) {
                let raw = String(line)
                if let range = raw.range(of: "KINGFALL_READY url=") {
                    let value = String(raw[range.upperBound...]).trimmingCharacters(in: .whitespaces)
                    if let url = URL(string: value) {
                        self.loadingLabel.isHidden = true
                        self.webView.isHidden = false
                        self.webView.load(URLRequest(url: url))
                    }
                }
            }
        }
    }

    private func stopServer() {
        started = false
        stdoutPipe?.fileHandleForReading.readabilityHandler = nil
        stderrPipe?.fileHandleForReading.readabilityHandler = nil
        if let process, process.isRunning {
            process.terminate()
            DispatchQueue.global().async {
                process.waitUntilExit()
            }
        }
        process = nil
        stdoutPipe = nil
        stderrPipe = nil
        stderrText = ""
    }

    private func pythonExecutable() -> String? {
        let candidates = ["/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    private func fail(_ message: String) {
        stopServer()
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "Start fehlgeschlagen"
        alert.informativeText = message
        alert.runModal()
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        if url.scheme == "file" {
            decisionHandler(.allow)
            return
        }
        if let host = url.host, host == "127.0.0.1" || host == "localhost" {
            decisionHandler(.allow)
            return
        }
        if url.scheme == "http" || url.scheme == "https" {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let url = navigationResponse.response.url {
            let path = url.path
            if path.contains("/api/va/audio/") {
                decisionHandler(.allow)
                return
            }
            if path.contains("/api/va/pack/") {
                decisionHandler(.download)
                return
            }
        }
        if navigationResponse.canShowMIMEType {
            decisionHandler(.allow)
        } else {
            decisionHandler(.download)
        }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.begin { result in
            completionHandler(result == .OK ? panel.urls : nil)
        }
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = suggestedFilename
        panel.begin { result in
            completionHandler(result == .OK ? panel.url : nil)
        }
    }
}

@main
enum HostApp {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate.shared
        app.delegate = delegate
        app.run()
    }
}
