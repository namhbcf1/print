import sys
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QLineEdit, QPushButton, QFileDialog, QMessageBox
from PyQt5.QtCore import QTimer, QThread
from PyQt5.QtGui import QImage, QPixmap
import cv2
import datetime
import os
from pyzbar.pyzbar import decode
from queue import Queue
import subprocess
import numpy as np

# Kiểm tra GPU
try:
    import cupy as cp
    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False

import subprocess

from PyQt5.QtCore import QThread
import subprocess
import queue

class RecorderThread(QThread):
    def __init__(self, width, height, fps, file_path1, file_path2, frame_queue1, frame_queue2, running):
        super().__init__() # Add super().__init__() to properly initialize QThread
        self.width = width
        self.height = height
        self.fps = fps
        self.file_path1 = file_path1
        self.file_path2 = file_path2
        self.frame_queue1 = frame_queue1
        self.frame_queue2 = frame_queue2
        self.running = running

    def run(self):
        order_code = datetime.datetime.now().strftime("%d%m%Y_%H%M%S")
        timestamp = datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S")

        # No need for drawtext_cmd1 and drawtext_cmd2 anymore

        cmd1 = [
            'ffmpeg', '-y',
            '-f', 'rawvideo',
            '-pix_fmt', 'bgr24',
            '-s', f'{self.width}x{self.height}',
            '-r', str(self.fps),
            '-i', '-',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-b:v', '10000k',
            '-g', '30',
            # Removed '-vf', drawtext_cmd1
            '-pix_fmt', 'yuv420p',
            self.file_path1
        ]

        cmd2 = [
            'ffmpeg', '-y',
            '-f', 'rawvideo',
            '-pix_fmt', 'bgr24',
            '-s', f'{self.width}x{self.height}',
            '-r', str(self.fps),
            '-i', '-',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-b:v', '10000k',
            '-g', '30',
            # Removed '-vf', drawtext_cmd2
            '-pix_fmt', 'yuv420p',
            self.file_path2
        ]

        proc1 = subprocess.Popen(cmd1, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        proc2 = subprocess.Popen(cmd2, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

        while self.running:
            try:
                frame1 = self.frame_queue1.get(timeout=1)
                # frame_np1 = np.frombuffer(frame1, dtype=np.uint8).reshape((self.height, self.width, 3)) # Convert frame bytes to NumPy array
                # text_to_add1 = f"Cam1 - {order_code} - {timestamp}"
                # cv2.putText(frame_np1, text_to_add1, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2) # Draw text using OpenCV
                # frame_with_text1 = frame_np1.tobytes() # Convert NumPy array back to bytes
                proc1.stdin.write(frame1.tobytes()) # Write frame directly to ffmpeg - frame1 already has text from update_frame
            except queue.Empty:
                pass

            try:
                frame2 = self.frame_queue2.get(timeout=1)
                # frame_np2 = np.frombuffer(frame2, dtype=np.uint8).reshape((self.height, self.width, 3)) # Convert frame bytes to NumPy array
                # text_to_add2 = f"Cam2 - {order_code} - {timestamp}"
                # cv2.putText(frame_np2, text_to_add2, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2) # Draw text using OpenCV
                # frame_with_text2 = frame_np2.tobytes() # Convert NumPy array back to bytes
                proc2.stdin.write(frame2.tobytes()) # Write frame directly to ffmpeg - frame2 already has text from update_frame
            except queue.Empty:
                pass

        proc1.stdin.close()
        proc2.stdin.close()

        proc1.wait()
        proc2.wait()


    def stop(self):
        self.running = False
        self.wait()





class PackRecordPro(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.msgbox = None
        self.folder = os.path.abspath(".")  # Thư mục hiện tại của ứng dụng
        self.cam1 = cv2.VideoCapture(0)
        self.cam2 = cv2.VideoCapture(1)
        self.cam1.set(cv2.CAP_PROP_FRAME_WIDTH, 2560)
        self.cam1.set(cv2.CAP_PROP_FRAME_HEIGHT, 1440)
        self.cam2.set(cv2.CAP_PROP_FRAME_WIDTH, 2560)
        self.cam2.set(cv2.CAP_PROP_FRAME_HEIGHT, 1440)
        if not os.path.exists(self.folder):
            os.makedirs(self.folder)
        self.frame_queue1 = Queue()
        self.frame_queue2 = Queue()
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_frame)
        self.timer.start(15)
        self.recording = False
        self.recorder_thread = None # Initialize recorder_thread to None

    def initUI(self):
        self.setWindowTitle("PackRecord Pro")
        self.setGeometry(100, 100, 1400, 600)

        self.label_cam1 = QLabel(self)
        self.label_cam1.setGeometry(10, 10, 640, 360)

        self.label_cam2 = QLabel(self)
        self.label_cam2.setGeometry(660, 10, 640, 360)

        self.entry_order_code = QLineEdit(self)
        self.entry_order_code.setGeometry(10, 450, 200, 90)
        self.entry_order_code.setPlaceholderText("Nhập hoặc quét mã đơn hàng")
        self.entry_order_code.returnPressed.connect(self.start_recording)


        self.btn_start = QPushButton("Bắt đầu quay", self)
        self.btn_start.setGeometry(220, 500, 120, 40)
        self.btn_start.clicked.connect(self.start_recording)

        self.btn_stop = QPushButton("Dừng quay", self)
        self.btn_stop.setGeometry(350,500, 120, 40)
        self.btn_stop.clicked.connect(self.stop_recording)

        self.btn_select_folder = QPushButton("Chọn thư mục", self)
        self.btn_select_folder.setGeometry(480, 500, 120, 40)
        self.btn_select_folder.clicked.connect(self.select_folder)

        self.btn_open_folder = QPushButton("Mở thư mục", self)
        self.btn_open_folder.setGeometry(610, 500, 120, 40)
        self.btn_open_folder.clicked.connect(self.open_folder)

        self.status_label = QLabel("Sẵn sàng", self)
        self.status_label.setGeometry(10, 530, 780, 30)

    def update_frame(self):
        ret1, frame1 = self.cam1.read()
        ret2, frame2 = self.cam2.read()
        timestamp = datetime.datetime.now().strftime("%d-%m-%Y %H:%M:%S")

        order_code = self.entry_order_code.text().strip()
        if not order_code:
            order_code = "UNKNOWN"

        if ret1:
            text1 = f"Cam1 - {order_code} - {timestamp}"
            cv2.putText(frame1, text1, (30, 50), cv2.FONT_HERSHEY_SIMPLEX,
                                    1, (0, 255, 0), 2, cv2.LINE_AA)

            preview1 = cv2.resize(frame1, (800, 450))
            preview1_rgb = cv2.cvtColor(preview1, cv2.COLOR_BGR2RGB)
            image1 = QImage(preview1_rgb.data, 800, 450, QImage.Format_RGB888)
            self.label_cam1.setPixmap(QPixmap.fromImage(image1))

            if self.recording:
                self.frame_queue1.put(frame1.copy())
            else:
                self.scan_barcode(frame1)  # 🔥 Quét mã QR khi không quay video

        if ret2:
            text2 = f"Cam2 - {order_code} - {timestamp}"
            cv2.putText(frame2, text2, (30, 50), cv2.FONT_HERSHEY_SIMPLEX,
                                    1, (0, 255, 0), 2, cv2.LINE_AA)

            preview2 = cv2.resize(frame2, (800, 450))
            preview2_rgb = cv2.cvtColor(preview2, cv2.COLOR_BGR2RGB)
            image2 = QImage(preview2_rgb.data, 800, 450, QImage.Format_RGB888)
            self.label_cam2.setPixmap(QPixmap.fromImage(image2))

            if self.recording:
                self.frame_queue2.put(frame2.copy())




    def scan_barcode(self, frame):
        try:
            barcodes = decode(frame)
            if barcodes:
                order_code = barcodes[0].data.decode('utf-8').strip()

                if order_code:
                    # Nếu chưa quay hoặc mã đơn hàng mới khác mã cũ → bắt đầu quay lại
                    if not self.recording or self.entry_order_code.text() != order_code:
                        self.entry_order_code.setText(order_code)
                        self.start_recording()  # ✅ Tự động quay ngay khi phát hiện mã QR
        except Exception as e:
            print("Barcode decode error:", e)


    def show_auto_close_message(self, title, message, timeout=3000):
        self.msgbox = QMessageBox(self)
        self.msgbox.setWindowTitle(title)
        self.msgbox.setText(message)
        self.msgbox.setStandardButtons(QMessageBox.Ok)  # nên có nút OK
        self.msgbox.show()

        QTimer.singleShot(timeout, self.msgbox.accept)  # dùng accept thay vì close





    def start_recording(self):
        if not self.recording:
            order_code = self.entry_order_code.text()
            if order_code:
                self.recording = True
                timestamp = datetime.datetime.now().strftime("%d_%m_%Y")
                self.file_path1 = f"{self.folder}/{timestamp}_{order_code}_Cam1.mp4"
                self.file_path2 = f"{self.folder}/{timestamp}_{order_code}_Cam2.mp4"

                # Get width, height, fps from camera or use default values
                width = int(self.cam1.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(self.cam1.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = 30 # You can get FPS from camera if needed: self.cam1.get(cv2.CAP_PROP_FPS) or use a default value

                self.recorder_thread = RecorderThread(
                    width=width, # Pass width
                    height=height, # Pass height
                    fps=fps,     # Pass fps
                    file_path1=self.file_path1,
                    file_path2=self.file_path2,
                    frame_queue1=self.frame_queue1,
                    frame_queue2=self.frame_queue2,
                    running=True # Pass running=True
                )
                self.recorder_thread.start()
                self.status_label.setText(f"Đang quay đơn hàng: {order_code}")
                self.show_auto_close_message("Thông báo", "Đã bắt đầu quay video.")

    def stop_recording(self):
        if self.recording:
            if self.recorder_thread is not None: # Check if recorder_thread is initialized
                self.recorder_thread.running = False
                self.recorder_thread.wait()
            self.recording = False
            msg = QMessageBox()
            msg.setText(f"Video đã lưu vào thư mục: {self.folder}\nBạn có muốn quay đơn hàng khác không?")
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            response = msg.exec_()
            if response == QMessageBox.Yes:
                self.entry_order_code.clear()
                self.status_label.setText("Sẵn sàng")
            else:
                self.status_label.setText("Đã dừng quay")
                self.entry_order_code.clear()

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu trữ")
        if folder:
            self.folder = folder
            QMessageBox.information(self, "Thông báo", f"Đã chọn thư mục:\n{folder}")

    def open_folder(self):
        if hasattr(self, 'folder') and self.folder:
            os.startfile(self.folder)
        else:
            QMessageBox.warning(self, "Thông báo", "Bạn chưa chọn thư mục lưu trữ.")



    def closeEvent(self, event):
        if self.recording:
            if self.recorder_thread is not None: # Check if recorder_thread is initialized
                self.recorder_thread.running = False
                self.recorder_thread.wait()
        self.cam1.release()
        self.cam2.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PackRecordPro()
    window.show()
    sys.exit(app.exec_())
