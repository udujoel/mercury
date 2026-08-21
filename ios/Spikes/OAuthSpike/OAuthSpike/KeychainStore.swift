import Foundation
import Security

/// Thin wrapper around the Security framework's generic-password keychain
/// item type. Never use UserDefaults for tokens — UserDefaults is an
/// unencrypted plist under the app sandbox and shows up in unencrypted
/// device backups. Keychain items are encrypted at rest and access is
/// gated by the accessibility class below.
enum KeychainStore {

    enum KeychainError: Error, LocalizedError {
        case unexpectedStatus(OSStatus)
        case unexpectedData

        var errorDescription: String? {
            switch self {
            case .unexpectedStatus(let status):
                let message = SecCopyErrorMessageString(status, nil) as String?
                return "Keychain error \(status): \(message ?? "unknown")"
            case .unexpectedData:
                return "Keychain returned data in an unexpected format."
            }
        }
    }

    /// Mercury's briefing generation is triggered by a server-side cron, not
    /// by the phone. But the refresh token also has to be readable by an
    /// iOS BGProcessingTask (Spike 7) that can legally run while the device
    /// is locked — for example a background sync kicked off overnight.
    /// `kSecAttrAccessibleAfterFirstUnlock` keeps the item encrypted until
    /// the user unlocks the device once after boot, then leaves it readable
    /// until the next reboot — including from background execution while
    /// the device is subsequently locked. This is the standard choice for
    /// any token a background task must read. We deliberately do NOT use
    /// `WhenUnlocked` (would break background reads while the phone is
    /// locked) or `Always`-family accessibility (deprecated, and broader
    /// than we need since we only need "since first unlock").
    private static let accessibility = kSecAttrAccessibleAfterFirstUnlock

    private static func baseQuery(service: String, account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    static func save(_ value: String, service: String, account: String) throws {
        guard let data = value.data(using: .utf8) else {
            throw KeychainError.unexpectedData
        }

        var query = baseQuery(service: service, account: account)
        query[kSecAttrAccessible as String] = accessibility

        // Try update first; if nothing exists yet, add.
        let attributesToUpdate: [String: Any] = [kSecValueData as String: data]
        let updateStatus = SecItemUpdate(
            baseQuery(service: service, account: account) as CFDictionary,
            attributesToUpdate as CFDictionary
        )

        if updateStatus == errSecItemNotFound {
            query[kSecValueData as String] = data
            let addStatus = SecItemAdd(query as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw KeychainError.unexpectedStatus(addStatus)
            }
        } else if updateStatus != errSecSuccess {
            throw KeychainError.unexpectedStatus(updateStatus)
        }
    }

    static func read(service: String, account: String) throws -> String? {
        var query = baseQuery(service: service, account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)

        switch status {
        case errSecSuccess:
            guard let data = result as? Data,
                  let string = String(data: data, encoding: .utf8) else {
                throw KeychainError.unexpectedData
            }
            return string
        case errSecItemNotFound:
            return nil
        default:
            throw KeychainError.unexpectedStatus(status)
        }
    }

    static func delete(service: String, account: String) throws {
        let query = baseQuery(service: service, account: account)
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError.unexpectedStatus(status)
        }
    }
}
