import SwiftUI
import SwiftData

@main
struct MercuryApp: App {

    @State private var appState = AppState()

    var sharedModelContainer: ModelContainer = {
        let schema = Schema([
            BriefingEntity.self, SegmentEntity.self, TurnEntity.self, SourceRefEntity.self,
        ])
        let configuration = ModelConfiguration(schema: schema, isStoredInMemoryOnly: false)
        do {
            return try ModelContainer(for: schema, configurations: [configuration])
        } catch {
            fatalError("Could not create SwiftData ModelContainer: \(error)")
        }
    }()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
                .preferredColorScheme(.dark)
        }
        .modelContainer(sharedModelContainer)
    }
}

/// Routes between onboarding and the main app based on `AppState`.
struct RootView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        if appState.hasCompletedOnboarding {
            HomeView()
        } else {
            WelcomeView()
        }
    }
}
