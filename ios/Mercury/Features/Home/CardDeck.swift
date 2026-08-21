import SwiftUI

/// The fanned stacked-card deck (PRD 3.3). Cards are offset vertically and
/// horizontally, rearmost card most inset. A vertical drag brings a card
/// forward with `matchedGeometryEffect`; the moved card animates to full
/// size while the others recede.
struct CardDeck<Content: View>: View {
    let cardCount: Int
    @Binding var frontIndex: Int
    @ViewBuilder let content: (Int) -> Content

    @Namespace private var namespace
    @State private var dragOffset: CGFloat = 0

    private let cardWidthFraction: CGFloat = 0.62
    private let cardAspect: CGFloat = 1.4     // height = width * 1.4
    private let stackStep: CGFloat = 18

    var body: some View {
        GeometryReader { proxy in
            let cardWidth = proxy.size.width * cardWidthFraction
            let cardHeight = cardWidth * cardAspect

            ZStack {
                ForEach(0..<cardCount, id: \.self) { index in
                    let depth = depthFromFront(index)
                    content(index)
                        .frame(width: cardWidth, height: cardHeight)
                        .matchedGeometryEffect(id: index, in: namespace)
                        .scaleEffect(1 - CGFloat(depth) * 0.045)
                        .offset(
                            x: CGFloat(depth) * stackStep,
                            y: CGFloat(depth) * stackStep * 1.4 + (depth == 0 ? dragOffset : 0)
                        )
                        .zIndex(Double(cardCount - depth))
                        .shadow(color: .black.opacity(0.5), radius: 20, y: 12)
                        .gesture(
                            DragGesture()
                                .onChanged { value in
                                    guard depth == 0 else { return }
                                    dragOffset = value.translation.height
                                }
                                .onEnded { value in
                                    guard depth == 0 else { return }
                                    withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                                        if value.translation.height > 40 {
                                            advance()
                                        }
                                        dragOffset = 0
                                    }
                                }
                        )
                        .onTapGesture {
                            guard depth != 0 else { return }
                            withAnimation(.spring(response: 0.4, dampingFraction: 0.8)) {
                                frontIndex = index
                            }
                        }
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height, alignment: .center)
        }
    }

    /// 0 = frontmost, increasing = further back in the fan.
    private func depthFromFront(_ index: Int) -> Int {
        (index - frontIndex + cardCount) % cardCount
    }

    private func advance() {
        frontIndex = (frontIndex + 1) % cardCount
    }
}

#Preview {
    struct PreviewHost: View {
        @State var front = 0
        var body: some View {
            ZStack {
                Color.black.ignoresSafeArea()
                CardDeck(cardCount: 4, frontIndex: $front) { index in
                    switch index {
                    case 0: BriefingCard(isPlaying: true, onTap: {})
                    case 1: WeatherCard()
                    case 2: CalendarCard()
                    default: InboxCard()
                    }
                }
            }
        }
    }
    return PreviewHost()
}
