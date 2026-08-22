import SwiftUI

struct HomeView: View {
    @Environment(AppState.self) private var appState
    @State private var frontIndex = 0
    @State private var showPlayer = false

    var body: some View {
        NavigationStack {
            ZStack {
                MercuryColor.canvas.ignoresSafeArea()

                CardDeck(cardCount: 4, frontIndex: $frontIndex) { index in
                    switch index {
                    case 0:
                        BriefingCard(isPlaying: appState.playback.isPlaying) {
                            Task {
                                await appState.playBriefing()
                                if case .ready = appState.loadState {
                                    showPlayer = true
                                }
                            }
                        }
                    case 1:
                        WeatherCard()
                    case 2:
                        CalendarCard()
                    default:
                        InboxCard()
                    }
                }

                if case .loading = appState.loadState {
                    ProgressView()
                        .tint(.white)
                        .padding(24)
                        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
                }

                if case .failed(let message) = appState.loadState {
                    VStack {
                        Spacer()
                        Text(message)
                            .font(MercuryType.caption)
                            .foregroundStyle(.red)
                            .padding()
                            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                            .padding(.horizontal)
                            .padding(.bottom, 40)
                    }
                }
            }
            .navigationDestination(isPresented: $showPlayer) {
                PlayerView()
            }
            // Covers playback started outside a direct tap on the greeting
            // card — the wake notification's "Play" action or tapping the
            // notification itself (PRD F-17), which drive
            // `AppState.playPrefetched` directly rather than going through
            // this view's own button handler.
            .onChange(of: appState.playback.isPlaying) { _, isPlaying in
                if isPlaying { showPlayer = true }
            }
        }
    }
}

#Preview {
    HomeView()
        .environment(AppState())
}
