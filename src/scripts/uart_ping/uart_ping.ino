// uart_ping.ino — Leonardo
// Подключить USB-UART конвертер: TX→pin0(RX1), RX→pin1(TX1), GND→GND
// Скетч читает байт из Serial1 и отправляет его обратно (эхо)

#define BAUD 9600

void setup() {
    Serial.begin(115200);
    Serial1.begin(BAUD);
    Serial.println("uart_ping ready");
}

void loop() {
    if (Serial1.available()) {
        uint8_t b = Serial1.read();
        Serial1.write(b);
        Serial.print("echo: ");
        Serial.println(b);
    }
}
