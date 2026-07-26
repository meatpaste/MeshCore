#pragma once

#ifdef WITH_WEATHER_STATION

#include "../NodePrefs.h"
#include <MeshCore.h>
#include <WiFiClientSecure.h>
#include <stdint.h>

struct WeatherData {
  bool valid = false;
  float temp_c = 0;
  float humidity_pct = 0;
  float wind_mph = 0;
  int wind_dir_deg = -1;
  int weather_code = -1;
  int32_t utc_offset_secs = 0;  // local UTC offset for weather_lat/weather_lon, from Open-Meteo
  uint32_t fetched_at = 0;  // millis() timestamp of last successful fetch
};

// Periodically fetches current weather from Open-Meteo over WiFi.
// poll() is non-blocking except for the infrequent HTTP GET itself.
class WeatherClient {
  enum State { IDLE, WIFI_CONNECTING };

  NodePrefs* _prefs;
  mesh::RTCClock* _rtc;
  WeatherData _data;
  State _state;
  unsigned long _next_action;
  bool _time_synced;
  WiFiClientSecure _client;

  bool doFetch();
  void syncTimeFromNTP();

public:
  WeatherClient(NodePrefs* prefs, mesh::RTCClock* rtc);

  void begin();
  void poll();

  const WeatherData& getData() const { return _data; }
  bool isEnabled() const;
  bool isWifiConnected() const;

  static const char* codeToLabel(int code);
};

#endif
