import Foundation
import Security

/// KeychainStore — helper mínimo sobre kSecClassGenericPassword (Fase 8D, paso D4).
///
/// Antes del hardening de esta fase, `vitals_token` vivía en UserDefaults —
/// legible por cualquier proceso con acceso al plist de la app (no cifrado en
/// reposo). El token es el secreto compartido con /api/ingest y /api/ecg
/// (equivalente a una API key), así que merece Keychain.
///
/// Uso: `KeychainStore.set(token, forKey: "vitals_token")`,
/// `KeychainStore.get(forKey: "vitals_token")`, `KeychainStore.delete(forKey:)`.
/// Todas las operaciones son síncronas (Keychain local, sin red) y nunca lanzan
/// — errores de Keychain degradan a nil/no-op (mismo criterio "nunca crashea"
/// del resto del repo).
enum KeychainStore {

    private static let service = "vitals"

    static func set(_ value: String, forKey key: String) {
        guard let data = value.data(using: .utf8) else { return }

        // Borra cualquier entrada previa para esta key (evita errSecDuplicateItem)
        // antes de insertar — más simple y robusto que SecItemUpdate con dos
        // caminos de error distintos.
        delete(forKey: key)

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: data,
            // Accesible solo con el dispositivo desbloqueado, y NUNCA migra a un
            // backup/restore en otro dispositivo (el token es específico de esta
            // instalación + servidor).
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]

        SecItemAdd(query as CFDictionary, nil)
    }

    static func get(forKey key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]

        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func delete(forKey key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
