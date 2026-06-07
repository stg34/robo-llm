// Библиотека для работы с дальномерами
#include "TFLidar.h"
#include <Wire.h>

// Serial-порт к которому подключён дальномер
#define LIDAR_SERIAL    Serial1

// I2C-адрес этого слейва
#define I2C_SLAVE_ADDR  0x08

TFLidar lidar;

// volatile — чтобы компилятор не кэшировал значение,
// т.к. переменная меняется в loop() и читается в прерывании Wire
volatile int16_t dist = 0;

// Callback: вызывается когда мастер запрашивает данные
void onRequest() {
  // Отправляем int как два байта (big-endian)
  Wire.write((uint8_t)(dist >> 8));    // старший байт
  Wire.write((uint8_t)(dist & 0xFF));  // младший байт
}

void setup() {
  // Serial.begin(9600);
  // while (!Serial);
  // Serial.println("Slave init OK");

  LIDAR_SERIAL.begin(115200);
  lidar.begin(&LIDAR_SERIAL);

  Wire.begin(I2C_SLAVE_ADDR);
  Wire.onRequest(onRequest);
}

void loop() {
  // lidar.getData() blocks until a full TF-Luna frame arrives (~1.2ms at 115200).
  // No delay needed: the serial read itself paces the loop at ~100+ Hz.
  int16_t tmp = 0;
  lidar.getData(tmp);
  noInterrupts();
  dist = tmp;
  interrupts();
}
