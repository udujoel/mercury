import SwiftUI

struct ScheduleSetupView: View {
    @Environment(AppState.self) private var appState
    @State private var wakeTime = Calendar.current.date(
        bySettingHour: 7, minute: 0, second: 0, of: .now
    ) ?? .now
    @State private var length: BriefingLengthOption = .standard

    enum BriefingLengthOption: String, CaseIterable, Identifiable {
        case express, standard, full
        var id: String { rawValue }
        var label: String {
            switch self {
            case .express: return "Express (~3 min)"
            case .standard: return "Standard (~7 min)"
            case .full: return "Full (~15 min)"
            }
        }
    }

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
                    ForEach(BriefingLengthOption.allCases) { option in
                        Text(option.label).tag(option)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 24)

                Spacer()

                Button {
                    appState.hasCompletedOnboarding = true
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
