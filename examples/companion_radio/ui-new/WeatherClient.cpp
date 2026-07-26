#include "WeatherClient.h"

#ifdef WITH_WEATHER_STATION

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <time.h>

#define WEATHER_HOST          "api.open-meteo.com"
#define WIFI_CONNECT_TIMEOUT  15000
#define WIFI_RETRY_DELAY      30000
#define FETCH_RETRY_DELAY     20000

WeatherClient::WeatherClient(NodePrefs* prefs, mesh::RTCClock* rtc)
  : _prefs(prefs), _rtc(rtc), _state(IDLE), _next_action(0), _time_synced(false) {
  _client.setInsecure();  // no cert pinning -- simplification for embedded use
}

void WeatherClient::begin() {
  _next_action = millis();  // fetch as soon as WiFi is up
}

bool WeatherClient::isEnabled() const {
  return _prefs->weather_enabled && _prefs->wifi_ssid[0] != 0;
}

bool WeatherClient::isWifiConnected() const {
  return WiFi.status() == WL_CONNECTED;
}

const char* WeatherClient::codeToLabel(int code) {
  // WMO weather interpretation codes (used by Open-Meteo)
  if (code == 0) return "Clear";
  if (code >= 1 && code <= 3) return "Cloudy";
  if (code == 45 || code == 48) return "Fog";
  if (code >= 51 && code <= 57) return "Drizzle";
  if (code >= 61 && code <= 67) return "Rain";
  if (code >= 71 && code <= 77) return "Snow";
  if (code >= 80 && code <= 82) return "Showers";
  if (code >= 85 && code <= 86) return "Snow";
  if (code >= 95 && code <= 99) return "Storm";
  return "Unknown";
}

void WeatherClient::syncTimeFromNTP() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  struct tm timeinfo;
  if (getLocalTime(&timeinfo, 5000)) {
    time_t now;
    time(&now);
    _rtc->setCurrentTime((uint32_t) now);
    _time_synced = true;
    Serial.printf("WeatherClient: NTP time sync OK, %04d-%02d-%02d %02d:%02d:%02d UTC\n",
      timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
      timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
  } else {
    Serial.println("WeatherClient: NTP time sync failed");
  }
}

bool WeatherClient::doFetch() {
  char url[256];
  snprintf(url, sizeof(url),
    "https://" WEATHER_HOST "/v1/forecast?latitude=%.4f&longitude=%.4f&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,weather_code&wind_speed_unit=mph&timezone=auto",
    (double) _prefs->weather_lat, (double) _prefs->weather_lon);

  Serial.printf("WeatherClient: GET %s\n", url);

  bool success = false;
  HTTPClient http;
  if (http.begin(_client, url)) {
    http.setTimeout(8000);
    int code = http.GET();
    Serial.printf("WeatherClient: HTTP status %d\n", code);
    if (code == HTTP_CODE_OK) {
      String body = http.getString();
      JsonDocument doc;
      DeserializationError err = deserializeJson(doc, body);
      if (err) {
        Serial.printf("WeatherClient: JSON parse error: %s\n", err.c_str());
      } else {
        JsonObject current = doc["current"];
        _data.temp_c        = current["temperature_2m"] | 0.0f;
        _data.humidity_pct   = current["relative_humidity_2m"] | 0.0f;
        _data.wind_mph       = current["wind_speed_10m"] | 0.0f;
        _data.wind_dir_deg   = current["wind_direction_10m"] | -1;
        _data.weather_code   = current["weather_code"] | -1;
        _data.utc_offset_secs = doc["utc_offset_seconds"] | 0;
        _data.fetched_at     = millis();
        _data.valid          = true;
        success = true;
      }
    } else if (code < 0) {
      Serial.printf("WeatherClient: HTTP error: %s\n", http.errorToString(code).c_str());
    }
    http.end();
  } else {
    Serial.println("WeatherClient: http.begin() failed");
  }
  return success;
}

void WeatherClient::poll() {
  if (!isEnabled()) {
    if (_state != IDLE) {
      WiFi.disconnect(true);
      _state = IDLE;
    }
    return;
  }

  unsigned long now = millis();

  switch (_state) {
    case IDLE:
      if ((long)(now - _next_action) >= 0) {
        if (WiFi.status() == WL_CONNECTED) {
          if (!_time_synced) syncTimeFromNTP();
          bool ok = doFetch();
          _next_action = now + (ok ? (_prefs->weather_interval_secs * 1000UL) : FETCH_RETRY_DELAY);
        } else {
          Serial.printf("WeatherClient: connecting to WiFi SSID '%s'\n", _prefs->wifi_ssid);
          WiFi.mode(WIFI_STA);
          WiFi.begin(_prefs->wifi_ssid, _prefs->wifi_password);
          _state = WIFI_CONNECTING;
          _next_action = now + WIFI_CONNECT_TIMEOUT;
        }
      }
      break;

    case WIFI_CONNECTING:
      if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("WeatherClient: WiFi connected, IP=%s\n", WiFi.localIP().toString().c_str());
        if (!_time_synced) syncTimeFromNTP();
        bool ok = doFetch();
        _state = IDLE;
        _next_action = now + (ok ? (_prefs->weather_interval_secs * 1000UL) : FETCH_RETRY_DELAY);
      } else if ((long)(now - _next_action) >= 0) {
        Serial.printf("WeatherClient: WiFi connect timed out (status=%d)\n", WiFi.status());
        // connect attempt timed out, back off and retry later
        _state = IDLE;
        _next_action = now + WIFI_RETRY_DELAY;
      }
      break;
  }
}

#endif
