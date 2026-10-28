import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Server address and access key, kept on the device.
class Settings extends ChangeNotifier {
  String server = '';
  String token = '';

  static const _serverKey = 'server';
  static const _tokenKey = 'token';

  /// In the browser the app is usually served by the sheet2xml server itself.
  static String get defaultServer => kIsWeb ? Uri.base.origin : '';

  bool get configured => server.isNotEmpty;

  Future<void> load() async {
    final p = await SharedPreferences.getInstance();
    server = p.getString(_serverKey) ?? defaultServer;
    token = p.getString(_tokenKey) ?? '';
    notifyListeners();
  }

  Future<void> save(String newServer, String newToken) async {
    server = newServer.trim().replaceAll(RegExp(r'/+$'), '');
    if (server.isNotEmpty && !server.startsWith('http')) server = 'http://$server';
    token = newToken.trim();
    final p = await SharedPreferences.getInstance();
    await p.setString(_serverKey, server);
    await p.setString(_tokenKey, token);
    notifyListeners();
  }
}
