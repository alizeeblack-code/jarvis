import AVFoundation
import UIKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    private var store: DeskStore!

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        // Chimes play like music: through the silent switch, alongside anything else playing.
        try? AVAudioSession.sharedInstance().setCategory(.playback, options: [.mixWithOthers])
        try? AVAudioSession.sharedInstance().setActive(true)

        store = DeskStore()
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.backgroundColor = .black
        window.rootViewController = DeskViewController(store: store)
        window.makeKeyAndVisible()
        self.window = window
        return true
    }

    func applicationDidBecomeActive(_ application: UIApplication) {
        // a status display that sleeps is no use; this only holds while the app is in front
        application.isIdleTimerDisabled = true
        try? AVAudioSession.sharedInstance().setActive(true)
        store.start()
    }

    func applicationWillResignActive(_ application: UIApplication) {
        application.isIdleTimerDisabled = false
        store.stop()   // also hands the screen brightness back
    }
}
