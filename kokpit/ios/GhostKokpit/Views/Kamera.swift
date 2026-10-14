#if os(iOS)
import SwiftUI
import UIKit

/// Kamera: UIImagePickerController sarmalayıcısı. Çekilen kare geri çağrıya gider.
struct Kamera: UIViewControllerRepresentable {
    var cekildi: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let p = UIImagePickerController()
        p.sourceType = UIImagePickerController.isSourceTypeAvailable(.camera) ? .camera : .photoLibrary
        p.delegate = context.coordinator
        return p
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Koordinator { Koordinator(self) }

    final class Koordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let ust: Kamera
        init(_ ust: Kamera) { self.ust = ust }
        func imagePickerController(_ picker: UIImagePickerController,
                                   didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let img = (info[.editedImage] ?? info[.originalImage]) as? UIImage { ust.cekildi(img) }
            ust.dismiss()
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { ust.dismiss() }
    }
}
#endif
