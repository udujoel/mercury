import SwiftUI

/// The last step: when, and how long.
///
/// It also has to say what happens NEXT, which it did not. Tapping "Done"
/// landed the user on a Home screen with a card that fetches nothing,
/// because there is no briefing until the overnight job has run once — a
/// first impression of an app that appears broken on the morning it was
/// installed.
struct ScheduleSetupView: View {
    @Environment(AppState.self) private var appState
    @State private var wakeTime = Calendar.current.date(
        bySettingHour: 7, minute: 0, second: 0, of: .now
    ) ?? .now
    @State private var length: BriefingLength = .standard

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: MercurySpace.comfortable) {
                Text("When and how long?")
                    .font(MercuryType.greeting)
                    .foregroundStyle(MercuryColor.ink.color)
                    .fixedSize(horizontal: false, vertical: true)

                DatePicker("Wake time", selection: $wakeTime,
                           displayedComponents: .hourAndMinute)
                    .datePickerStyle(.wheel)
                    .labelsHidden()
                    .frame(maxWidth: .infinity)

                Picker("Length", selection: $length) {
                    ForEach(BriefingLength.allCases) { option in
                        Text(option.label).tag(option)
                    }
                }
                .pickerStyle(.segmented)

                // The empty first morning, said before it happens.
                //
                // There is no briefing until the overnight job has run once,
                // so a new user finishing onboarding at noon reaches a Home
                // screen whose card fetches nothing. Explaining that here
                // costs one paragraph; discovering it costs the first
                // impression.
                VStack(alignment: .leading, spacing: MercurySpace.tight) {
                    Text("Your first briefing")
                        .font(MercuryType.cardTitle)
                        .foregroundStyle(MercuryColor.ink.color)
                    Text("Briefings are made overnight, so the first one "
                         + "arrives tomorrow morning. If you would rather not "
                         + "wait, Home has a button to build one now from "
                         + "today\u{2019}s mail.")
                        .font(MercuryType.cardBody)
                        .foregroundStyle(MercuryColor.inkMuted.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(MercurySpace.comfortable)
                .mercuryCard(.raised)

                Button {
                    let components = Calendar.current.dateComponents([.hour, .minute], from: wakeTime)
                    appState.wakeTimeMinutesFromMidnight =
                        (components.hour ?? 7) * 60 + (components.minute ?? 0)
                    appState.briefingLength = length
                    appState.hasCompletedOnboarding = true

                    // Request notification authorization here rather than
                    // waiting for the next cold launch — this is the first
                    // moment the app has a wake time to schedule a
                    // SECONDARY-layer prefetch window around (PRD 3.6), and
                    // asking right after onboarding is when the user has
                    // the most context for what the permission is for.
                    Task {
                        await PushService.shared.requestAuthorization()
                        BackgroundScheduler.schedule(wakeTime: appState.nextWakeDate)
                    }
                } label: {
                    OnboardingButtonLabel("Done")
                }
            }
            .mercuryGutter()
            .padding(.vertical, MercurySpace.section)
        }
        .background(MercuryColor.surface.color.ignoresSafeArea())
        .navigationBarTitleDisplayMode(.inline)
    }
}

#Preview {
    ScheduleSetupView()
        .environment(AppState())
}
