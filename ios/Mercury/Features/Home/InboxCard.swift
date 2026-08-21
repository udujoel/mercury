import SwiftUI

struct InboxCard: View {
    struct EmailPreview: Identifiable {
        let id = UUID()
        let subject: String
        let from: String
    }

    let emails: [EmailPreview]

    init(emails: [EmailPreview] = InboxCard.placeholder) {
        self.emails = emails
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            AmbientBackground(seed: "inbox", family: .inbox)

            Image(systemName: "line.3.horizontal.decrease.circle")
                .foregroundStyle(MercuryColor.textPrimary)
                .padding(16)

            VStack(alignment: .leading, spacing: 10) {
                Text("Your inbox")
                    .font(MercuryType.cardTitle)
                    .foregroundStyle(MercuryColor.inboxAccent)
                ForEach(emails.prefix(3)) { email in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "envelope.fill")
                            .font(.caption2)
                            .foregroundStyle(MercuryColor.inboxAccent)
                            .padding(.top, 3)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(email.subject)
                                .font(MercuryType.cardBody.bold())
                                .foregroundStyle(MercuryColor.textPrimary)
                                .lineLimit(1)
                                .truncationMode(.tail)
                            Text(email.from)
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

    static let placeholder: [EmailPreview] = [
        EmailPreview(subject: "Q3 planning doc ready for review", from: "Priya Shah"),
        EmailPreview(subject: "Invoice #4471 due Friday", from: "Billing"),
        EmailPreview(subject: "Re: onboarding feedback", from: "Sam Ortiz"),
    ]
}

#Preview {
    InboxCard()
        .frame(width: 260, height: 360)
        .background(Color.black)
}
