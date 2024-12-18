import time
import qrcode
import os
import random
def generate_qrcode():
    while True:
        number=random.random()
        img = qrcode.make(str(number))    # 將隨機數轉換成QRCode
        img.save('qrcode.png') 
        time.sleep(15)
        os.remove("qrcode.png")
generate_qrcode()