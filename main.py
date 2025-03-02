import sys
import os
import cv2
import time
import threading
import queue
from datetime import datetime, timedelta
import numpy as np
from pyzbar.pyzbar import decode, ZBarSymbol

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QMutex
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QWidget, QMessageBox, QFileDialog, QLineEdit
)

# ------------------------------
# WriterThread: Luồng ghi video sử dụng queue
# ------------------------------
class WriterThread(threading.Thread):
    def __init__(self, video_writer, frame_queue):
        super().__init__()
        self.video_writer = video_writer
        self.frame_queue = frame_queue
        self.running = True

    def run(self):
        while self.running:
            frame = self.frame_queue.get()
            if frame is None:  # tín hiệu dừng
                break
            self.video_writer.write(frame)
            self.frame_queue.task_done()
        while not self.frame_queue.empty():
            frame = self.frame_queue.get()
            if frame is not None:
                self.video_writer.write(frame)
            self.frame_queue.task_done()
        self.video_writer.release()

    def stop(self):
        self.running = False
        self.frame_queue.put(None)
        self.join()

# ------------------------------
# VideoCaptureThread: Đọc frame từ webcam
# ------------------------------
class VideoCaptureThread(QThread):
    newFrameSignal = pyqtSignal()

    def __init__(self, camera_index=0, parent=None):
        super().__init__(parent)
        self.camera_index = camera_index
        self.running = True
        # Mở webcam với backend DirectShow, đặt codec MJPG để tối đa FPS
        self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        # Sử dụng độ phân giải 1280x720 để giảm tải
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_FPS, 60)

        self.latest_frame = None
        self.mutex = QMutex()

    def run(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                self.mutex.lock()
                self.latest_frame = frame
                self.mutex.unlock()
                self.newFrameSignal.emit()
            self.msleep(16)  # ~60 fps

    def stop(self):
        self.running = False
        self.wait()
        self.cap.release()

    def get_frame(self):
        self.mutex.lock()
        f = None
        if self.latest_frame is not None:
            f = self.latest_frame.copy()
        self.mutex.unlock()
        return f

# ------------------------------
# MainWindow: Giao diện chính
# ------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Phần mềm Đóng Gói Hàng Hóa - Quét mã và tự động quay video")
        self.setGeometry(100, 100, 1280, 720)
        self.setStyleSheet("""
            QMainWindow { background-color: #2E2E2E; }
            QPushButton {
                background-color: #4CAF50; 
                color: white; 
                border: none; 
                padding: 10px 20px; 
                font-size: 16px; 
                border-radius: 5px;
            }
            QPushButton:hover { background-color: #45a049; }
            QLabel { font-size: 14px; color: white; }
            QLineEdit { font-size: 16px; padding: 5px; }
        """)

        # Thư mục lưu video
        self.output_folder = os.path.join(os.getcwd(), "recorded_videos")
        if not os.path.exists(self.output_folder):
            os.makedirs(self.output_folder)

        # Các trạng thái ghi video
        self.manual_recording = False
        self.manual_writer_thread = None
        self.manual_queue = None

        self.auto_recording = False
        self.auto_writer_thread = None
        self.auto_queue = None
        self.auto_start_time = None
        self.auto_duration = 10  # ghi tự động 10 giây
        self.last_code_data = None

        # Widget preview
        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background-color: black;")
        self.video_label.setFixedSize(640, 360)

        # Trường nhập từ máy quét barcode (hoặc bàn phím)
        self.scanner_input = QLineEdit()
        self.scanner_input.setPlaceholderText("Quét mã vạch vào đây...")
        # Khi người dùng nhấn Enter, bắt đầu auto-record
        self.scanner_input.returnPressed.connect(self.handle_scanner_input)

        # Các nút điều khiển
        self.btn_start_manual = QPushButton("Bắt đầu Ghi Thủ Công")
        self.btn_stop_manual = QPushButton("Dừng Ghi Thủ Công")
        self.btn_stop_auto = QPushButton("Dừng Quay Tự Động")
        self.btn_open_folder = QPushButton("Mở Thư Mục Video")
        self.btn_settings = QPushButton("Cài Đặt")

        self.btn_start_manual.clicked.connect(self.start_manual_recording)
        self.btn_stop_manual.clicked.connect(self.stop_manual_recording)
        self.btn_stop_auto.clicked.connect(self.stop_auto_recording)
        self.btn_open_folder.clicked.connect(self.open_video_folder)
        self.btn_settings.clicked.connect(self.open_settings)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.btn_start_manual)
        btn_layout.addWidget(self.btn_stop_manual)
        btn_layout.addWidget(self.btn_stop_auto)
        btn_layout.addWidget(self.btn_open_folder)
        btn_layout.addWidget(self.btn_settings)

        main_layout = QVBoxLayout()
        main_layout.addWidget(self.video_label)
        main_layout.addWidget(self.scanner_input)
        main_layout.addLayout(btn_layout)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        # Khởi tạo luồng capture video
        self.capture_thread = VideoCaptureThread(camera_index=0)
        self.capture_thread.newFrameSignal.connect(self.on_new_frame)
        self.capture_thread.start()

        # Timer decode (30 fps)
        self.decode_timer = QTimer(self)
        self.decode_timer.setInterval(33)
        self.decode_timer.timeout.connect(self.decode_and_check)
        self.decode_timer.start()

    def handle_scanner_input(self):
        """Xử lý dữ liệu từ máy quét barcode (hoặc bàn phím)."""
        code = self.scanner_input.text().strip()
        if code:
            print(f"[Scanner] Phát hiện mã: {code}")
            # Bắt đầu auto-record nếu chưa đang quay
            if not self.auto_recording:
                self.last_code_data = code
                self.start_auto_recording(code)
            self.scanner_input.clear()

    def on_new_frame(self):
        """Cập nhật preview khi có frame mới."""
        frame = self.capture_thread.get_frame()
        if frame is None:
            return
        disp = frame.copy()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(disp, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    1, (0, 0, 255), 2)
        preview = cv2.resize(disp, (self.video_label.width(), self.video_label.height()),
                             interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(preview, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qt_image = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        self.video_label.setPixmap(QPixmap.fromImage(qt_image))

        # Nếu ghi manual, đưa frame vào queue
        if self.manual_recording and self.manual_queue:
            try:
                self.manual_queue.put_nowait(frame.copy())
            except queue.Full:
                pass
        # Nếu ghi auto, đưa frame vào queue
        if self.auto_recording and self.auto_queue:
            try:
                self.auto_queue.put_nowait(frame.copy())
            except queue.Full:
                pass

    def decode_and_check(self):
        """Decode mã vạch (1D/2D) từ frame mới nhất và kích hoạt auto-record nếu phát hiện mã mới."""
        frame = self.capture_thread.get_frame()
        if frame is None:
            return

        results = decode(frame, symbols=[
            ZBarSymbol.CODE128, ZBarSymbol.CODE39, ZBarSymbol.EAN13,
            ZBarSymbol.UPCA, ZBarSymbol.UPCE, ZBarSymbol.QRCODE
        ])
        if not results:
            return

        code_obj = results[0]
        code_data = code_obj.data.decode('utf-8').strip()
        code_type = code_obj.type
        print(f"[pyzbar] Phát hiện mã: {code_data}, loại: {code_type}")

        if code_data and code_data != self.last_code_data and not self.auto_recording:
            self.last_code_data = code_data
            self.start_auto_recording(code_data)

    # ------------------------------
    # Manual Recording
    # ------------------------------
    def start_manual_recording(self):
        if self.manual_recording:
            QMessageBox.warning(self, "Cảnh báo", "Đang ghi video thủ công!")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Chọn file lưu video thủ công",
            os.path.join(self.output_folder, "manual_video.mp4"),
            "MP4 Files (*.mp4);;All Files (*)"
        )
        if file_path:
            cap = self.capture_thread.cap
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps < 1:
                fps = 60.0
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(file_path, fourcc, fps, (width, height))
            if not writer.isOpened():
                QMessageBox.critical(self, "Lỗi", "Không mở được VideoWriter!")
                return
            self.manual_queue = queue.Queue(maxsize=120)
            self.manual_writer_thread = WriterThread(writer, self.manual_queue)
            self.manual_writer_thread.start()
            self.manual_recording = True
            QMessageBox.information(self, "Thông báo", "Đã bắt đầu ghi video thủ công.")

    def stop_manual_recording(self):
        if self.manual_recording:
            self.manual_recording = False
            if self.manual_writer_thread:
                self.manual_writer_thread.stop()
                self.manual_writer_thread = None
            self.manual_queue = None
            QMessageBox.information(self, "Thông báo", "Đã dừng ghi video thủ công.")
            reply = QMessageBox.question(self, "Tiếp tục quay?",
                                         "Bạn có muốn quay video mới không?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                QMessageBox.information(self, "Thông báo", "Bạn có thể bấm 'Bắt đầu Ghi Thủ Công' để quay video mới.")
            else:
                QMessageBox.information(self, "Thông báo", "Không quay thêm video mới.")
        else:
            QMessageBox.warning(self, "Thông báo", "Chưa ghi video thủ công.")

    # ------------------------------
    # Auto Recording
    # ------------------------------
    def start_auto_recording(self, code_data):
        """Bắt đầu auto-record 10 giây, lưu file theo định dạng ddmmyyyy_{code_data}.mp4."""
        cap = self.capture_thread.cap
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps < 1:
            fps = 60.0

        date_str = datetime.now().strftime("%d%m%Y")
        file_name = f"{date_str}_{code_data}.mp4"
        file_path = os.path.join(self.output_folder, file_name)

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(file_path, fourcc, fps, (width, height))
        if not writer.isOpened():
            QMessageBox.critical(self, "Lỗi", "Không mở được VideoWriter auto!")
            return

        self.auto_queue = queue.Queue(maxsize=120)
        self.auto_writer_thread = WriterThread(writer, self.auto_queue)
        self.auto_writer_thread.start()
        self.auto_recording = True
        self.auto_start_time = datetime.now()
        QMessageBox.information(self, "Thông báo",
                                f"Đã tự động ghi video cho mã: {code_data}\n"
                                "Sau 10 giây sẽ dừng, hoặc bạn có thể nhấn 'Dừng Quay Tự Động'.")

    def stop_auto_recording(self):
        if self.auto_recording:
            self.auto_recording = False
            if self.auto_writer_thread:
                self.auto_writer_thread.stop()
                self.auto_writer_thread = None
            self.auto_queue = None
            self.last_code_data = None
            QMessageBox.information(self, "Thông báo", "Đã dừng auto ghi video.")
        else:
            QMessageBox.warning(self, "Thông báo", "Chưa quay auto hoặc đã dừng.")

    # ------------------------------
    # Mở thư mục, cài đặt, đóng
    # ------------------------------
    def open_video_folder(self):
        QFileDialog.getExistingDirectory(self, "Mở Thư Mục Video", self.output_folder)

    def open_settings(self):
        QMessageBox.information(self, "Cài Đặt", "Chức năng cài đặt sẽ được phát triển sau.")

    def closeEvent(self, event):
        self.capture_thread.stop()
        if self.manual_writer_thread:
            self.manual_writer_thread.stop()
        if self.auto_writer_thread:
            self.auto_writer_thread.stop()
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
