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
        }
    }
}

#Preview {
    HomeView()
        .environment(AppState())
}
