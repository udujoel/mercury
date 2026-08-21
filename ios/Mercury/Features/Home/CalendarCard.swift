import SwiftUI

struct CalendarCard: View {
    struct Event: Identifiable {
        let id = UUID()
        let title: String
        let timeRange: String
    }

    let events: [Event]

    init(events: [Event] = CalendarCard.placeholder) {
        self.events = events
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            AmbientBackground(seed: "calendar", family: .calendar)

            Image(systemName: "line.3.horizontal.decrease.circle")
                .foregroundStyle(MercuryColor.textPrimary)
                .padding(16)

            VStack(alignment: .leading, spacing: 10) {
                Text("Your day")
                    .font(MercuryType.cardTitle)
                    .foregroundStyle(MercuryColor.calendarAccent)
                ForEach(events.prefix(3)) { event in
                    HStack(alignment: .top, spacing: 8) {
                        Circle()
                            .fill(MercuryColor.calendarAccent)
                            .frame(width: 6, height: 6)
                            .padding(.top, 6)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(event.title)
                                .font(MercuryType.cardBody)
                                .foregroundStyle(MercuryColor.textPrimary)
                                .lineLimit(1)
                                .truncationMode(.tail)
                            Text(event.timeRange)
                                .font(MercuryType.caption)
                                .foregroundStyle(MercuryColor.textSecondary)
                        }
                    }
                }
                Spacer()
            }
            .padding(20)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .clipShape(RoundedRectangle(cornerRadius: MercuryColor.cardCornerRadius, style: .continuous))
    }

    static let placeholder: [Event] = [
        Event(title: "Standup", timeRange: "9:00 – 9:15 AM"),
        Event(title: "1:1 with Priya", timeRange: "11:00 – 11:30 AM"),
        Event(title: "Design review", timeRange: "2:00 – 3:00 PM"),
    ]
}

#Preview {
    CalendarCard()
        .frame(width: 260, height: 360)
        .background(Color.black)
}
