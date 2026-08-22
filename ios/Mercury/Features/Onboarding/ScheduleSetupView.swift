import SwiftUI

struct ScheduleSetupView: View {
    @Environment(AppState.self) private var appState
    @State private var wakeTime = Calendar.current.date(
        bySettingHour: 7, minute: 0, second: 0, of: .now
    ) ?? .now
    @State private var length: BriefingLength = .standard

    var body: some View {
        ZStack {
            MercuryColor.canvas.ignoresSafeArea()
            VStack(spacing: 28) {
                Text("When and how long?")
                    .font(MercuryType.cardTitle)
                    .foregroundStyle(MercuryColor.textPrimary)
                    .padding(.top, 64)

                DatePicker("Wake time", selection: $wakeTime, displayedComponents: .hourAndMinute)
                    .datePickerStyle(.wheel)
                    .colorScheme(.dark)
                    .labelsHidden()

                Picker("Length", selection: $length) {
                    ForEach(BriefingLength.allCases) { option in
                        Text(option.label).tag(option)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 24)

                Spacer()

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
                    Text("Done")
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
    }
}

#Preview {
    ScheduleSetupView()
        .environment(AppState())
}
