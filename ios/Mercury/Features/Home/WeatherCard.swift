import SwiftUI

struct WeatherCard: View {
    var body: some View {
        ZStack(alignment: .bottomLeading) {
            AmbientBackground(seed: "weather", family: .weather)

            VStack(alignment: .leading, spacing: 6) {
                Text("64°")
                    .font(.system(size: 44, weight: .light))
                    .foregroundStyle(MercuryColor.textPrimary)
                Text("Partly cloudy")
                    .font(MercuryType.cardBody)
                    .foregroundStyle(MercuryColor.textSecondary)

                HStack(spacing: 14) {
                    ForEach(["9am", "12pm", "3pm"], id: \.self) { hour in
                        VStack(spacing: 2) {
                            Text(hour).font(MercuryType.caption)
                            Text("☀️").font(.caption)
                        }
                        .foregroundStyle(MercuryColor.textSecondary)
                    }
                }
                .padding(.top, 4)

                HStack(spacing: 4) {
                    Image(systemName: "mappin")
                    Text("San Francisco")
                }
                .font(MercuryType.caption)
                .foregroundStyle(MercuryColor.weatherAccent)
            }
            .padding(20)
        }
        .clipShape(RoundedRectangle(cornerRadius: MercuryColor.cardCornerRadius, style: .continuous))
    }
}

#Preview {
    WeatherCard()
        .frame(width: 260, height: 360)
        .background(Color.black)
}
