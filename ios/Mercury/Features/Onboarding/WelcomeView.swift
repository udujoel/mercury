import SwiftUI

struct WelcomeView: View {
    var body: some View {
        NavigationStack {
            ZStack {
                MercuryColor.canvas.ignoresSafeArea()
                VStack(spacing: 24) {
                    Spacer()
                    Text("Mercury")
                        .font(MercuryType.greeting)
                        .foregroundStyle(MercuryColor.textPrimary)
                    Text("Your morning, narrated.")
                        .font(MercuryType.cardBody)
                        .foregroundStyle(MercuryColor.textSecondary)
                    Spacer()
                    NavigationLink {
                        ConnectAccountsView()
                    } label: {
                        Text("Get started")
                            .font(MercuryType.cardTitle)
                            .foregroundStyle(.black)
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(MercuryColor.textPrimary, in: Capsule())
                    }
                    .padding(.horizontal, 32)
                    .padding(.bottom, 48)
                }
            }
            .navigationBarHidden(true)
        }
    }
}

#Preview {
    WelcomeView()
}
