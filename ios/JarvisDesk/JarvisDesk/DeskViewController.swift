import SwiftUI
import UIKit

/// Jarvis's desk display, drawn natively from GET /api/desk: full screen, landscape, always on.
/// Press and hold with two fingers to change the address.
final class DeskViewController: UIHostingController<DeskView> {
    let store: DeskStore

    init(store: DeskStore) {
        self.store = store
        super.init(rootView: DeskView(store: store))
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not from a storyboard") }

    override var prefersStatusBarHidden: Bool { true }
    override var prefersHomeIndicatorAutoHidden: Bool { true }
    override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .landscape }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        let hold = UILongPressGestureRecognizer(target: self, action: #selector(askAddress(_:)))
        hold.numberOfTouchesRequired = 2
        hold.minimumPressDuration = 1.5
        view.addGestureRecognizer(hold)
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        if store.address == nil { askAddress(nil) }
    }

    @objc private func askAddress(_ g: UILongPressGestureRecognizer?) {
        if let g = g, g.state != .began { return }
        guard presentedViewController == nil else { return }
        let alert = UIAlertController(
            title: tr("Jarvis 地址", "Jarvis address"),
            message: tr("电脑上 Jarvis 页头「手机访问」里的链接（带 token）",
                        "The link from the phone button in Jarvis's header (with its token)"),
            preferredStyle: .alert)
        alert.addTextField { f in
            f.text = self.store.address?.absoluteString
            f.keyboardType = .URL
            f.autocapitalizationType = .none
            f.autocorrectionType = .no
            f.clearButtonMode = .whileEditing
        }
        alert.addAction(UIAlertAction(title: tr("取消", "Cancel"), style: .cancel))
        alert.addAction(UIAlertAction(title: tr("连接", "Connect"), style: .default) { [weak self] _ in
            self?.store.setAddress(alert.textFields?.first?.text ?? "")
        })
        present(alert, animated: true)
    }
}
