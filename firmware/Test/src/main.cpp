#include <Arduino.h>

// 定義 MQ-2 AO (類比輸出) 接腳
const int MQ2_AO_PIN = 34;

void setup() {
  // 初始化序列埠通訊
  Serial.begin(115200);
  
  // 設定腳位模式為輸入
  pinMode(MQ2_AO_PIN, INPUT);
  
  Serial.println("\n====================================");
  Serial.println("MQ-2 氣體感測器單獨測試程式");
  Serial.println("預熱提示：上電前 20~30 秒讀值會逐漸趨於穩定");
  Serial.println("====================================\n");
  delay(2000);
}

void loop() {
  // 1. 讀取 ESP32 的 ADC 原始數值 (0 ~ 4095)
  int rawValue = analogRead(MQ2_AO_PIN);
  
  // 2. 計算 ESP32 腳位上的實際電壓 (0 ~ 3.3V)
  float pinVoltage = (rawValue / 4095.0) * 3.3;

  // 3. 換算 MQ-2 模組真實輸出的電壓 (考慮兩顆 10k 電阻對半分壓，故乘 2)
  float mq2Voltage = pinVoltage * 2.0;

  // 4. 印出結果至 Serial Monitor
  Serial.print("ADC 原始數值 (0-4095): ");
  Serial.print(rawValue);
  Serial.print(" | 腳位電壓: ");
  Serial.print(pinVoltage, 2);
  Serial.print(" V | MQ-2真實電壓: ");
  Serial.print(mq2Voltage, 2);
  Serial.println(" V");

  // 簡單的濃度增加判斷 (數值可依現場環境調校)
  if (rawValue > 2000) {
    Serial.println("  ⚠️ 警告：偵測到易燃氣體或煙霧濃度升高！");
  }

  delay(1000); // 每秒更新一次
}