import 'dart:convert';
import 'dart:io';
import '../models/protocol.dart';

/// Zero-dependency HTTP client communicating with the local RYNA engine on 127.0.0.1.
class RynaApiClient {
  final String baseUrl;
  final HttpClient _http = HttpClient();

  RynaApiClient({this.baseUrl = 'http://127.0.0.1:8765'});

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Map<String, dynamic>? body,
  }) async {
    final uri = Uri.parse('$baseUrl$path');
    final req = await _http.openUrl(method, uri);
    req.headers.contentType = ContentType.json;
    if (body != null) {
      req.write(jsonEncode(body));
    }
    final resp = await req.close();
    final raw = await resp.transform(utf8.decoder).join();
    final parsed = raw.isEmpty ? <String, dynamic>{} : jsonDecode(raw) as Map<String, dynamic>;
    if (resp.statusCode >= 400) {
      throw Exception(parsed['error'] ?? 'HTTP ${resp.statusCode}');
    }
    return parsed;
  }

  Future<RynaStatus> fetchStatus() async =>
      RynaStatus.fromJson(await _request('GET', '/api/v1/status'));

  Future<Map<String, dynamic>> sendChat(String? conversationId, String message) =>
      _request('POST', '/api/v1/chat', body: {
        'conversation_id': conversationId,
        'message': message,
      });

  Future<List<RynaMessage>> fetchMessages(String conversationId) async {
    final data = await _request('GET', '/api/v1/conversations/$conversationId/messages');
    final items = (data['messages'] as List<dynamic>? ?? []);
    return items.map((e) => RynaMessage.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<List<RynaTask>> fetchTasks() async {
    final data = await _request('GET', '/api/v1/tasks');
    return (data['tasks'] as List<dynamic>? ?? [])
        .map((e) => RynaTask.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<List<RynaMemory>> fetchMemories() async {
    final data = await _request('GET', '/api/v1/memories');
    return (data['memories'] as List<dynamic>? ?? [])
        .map((e) => RynaMemory.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> fetchSettings() async {
    final data = await _request('GET', '/api/v1/settings');
    return Map<String, dynamic>.from(data['settings'] as Map? ?? {});
  }

  Future<void> updateSettings(Map<String, dynamic> patch) async {
    await _request('PUT', '/api/v1/settings', body: {'settings': patch});
  }

  Future<void> setCompanionState(String state) async {
    await _request('POST', '/api/v1/voice/state', body: {'state': state});
  }

  Future<void> triggerEmergencyStop() async {
    await _request('POST', '/api/v1/emergency-stop', body: {});
  }
}
