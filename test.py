import time
import qrcode
import os
import random
while True:
    number=random.random()
    img = qrcode.make(str(number))    # 要轉換成 QRCode 的文字
    img.save('qrcode.png') 
    time.sleep(15)
    os.remove("qrcode.png")
