#pragma once

#include <helpers/ui/DisplayDriver.h>
#include <helpers/CommonCLI.h>
#include "WeatherClient.h"

class UITask {
  DisplayDriver* _display;
  unsigned long _next_read, _next_refresh, _auto_off;
  int _prevBtnState;
  NodePrefs* _node_prefs;
  char _version_info[32];
#ifdef WITH_WEATHER_STATION
  WeatherClient* _weather;
  bool _weather_page;
  uint32_t _last_weather_fetch;
#endif

  void renderCurrScreen();
public:
  UITask(DisplayDriver& display) : _display(&display) { _next_read = _next_refresh = 0; }
  void begin(NodePrefs* node_prefs, const char* build_date, const char* firmware_version
#ifdef WITH_WEATHER_STATION
      , WeatherClient* weather = NULL
#endif
    );

  void loop();
};