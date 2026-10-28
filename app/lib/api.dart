import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

/// A page or PDF to upload.
class Upload {
  Upload(this.name, this.bytes);
  final String name;
  final Uint8List bytes;

  bool get isPdf => name.toLowerCase().endsWith('.pdf');
}

class Health {
  Health({required this.version, required this.omr, required this.review, required this.token});
  final String version;
  final bool omr;
  final bool review;
  final bool token;
}

class Job {
  Job.fromJson(Map<String, dynamic> j)
    : id = j['id'] as String,
      status = j['status'] as String,
      messages = List<String>.from(j['messages'] as List? ?? const []),
      error = j['error'] as String? ?? '',
      result = j['result'] as String?,
      filename = (j['filename'] as String?)?.isNotEmpty == true ? j['filename'] as String : 'noten.musicxml',
      engine = j['engine'] as String? ?? '',
      title = j['title'] as String? ?? '',
      parts = List<String>.from(j['parts'] as List? ?? const []),
      measures = j['measures'] as int? ?? 0,
      flaggedMeasures = j['flagged_measures'] as int? ?? 0,
      warnings = List<String>.from(j['warnings'] as List? ?? const []);

  final String id;
  final String status; // queued, running, done, error
  final List<String> messages;
  final String error;
  final String? result;
  final String filename;
  final String engine; // vector, omr
  final String title;
  final List<String> parts;
  final int measures;
  final int flaggedMeasures;
  final List<String> warnings;

  bool get finished => status == 'done' || status == 'error';
}

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Client for the sheet2xml server (see README, section API).
class Api {
  Api(String baseUrl, this.token) : base = Uri.parse(baseUrl.endsWith('/') ? baseUrl : '$baseUrl/');

  final Uri base;
  final String token;

  Map<String, String> get _headers => token.isEmpty ? {} : {'Authorization': 'Bearer $token'};

  Future<Map<String, dynamic>> _json(http.Response r) async {
    Map<String, dynamic> body;
    try {
      body = jsonDecode(utf8.decode(r.bodyBytes)) as Map<String, dynamic>;
    } catch (_) {
      throw ApiException('Unerwartete Antwort vom Server (${r.statusCode})');
    }
    if (r.statusCode >= 400) {
      throw ApiException(body['detail']?.toString() ?? 'Fehler ${r.statusCode}');
    }
    return body;
  }

  Future<Health> health() async {
    final r = await http.get(base.resolve('api/health')).timeout(const Duration(seconds: 10));
    final j = await _json(r);
    return Health(
      version: j['version'] as String? ?? '',
      omr: j['omr'] as bool? ?? false,
      review: j['review'] as bool? ?? false,
      token: j['token'] as bool? ?? false,
    );
  }

  Future<Job> createJob(List<Upload> files, {bool review = false}) async {
    final req = http.MultipartRequest('POST', base.resolve('api/jobs'))
      ..headers.addAll(_headers)
      ..fields['review'] = review ? 'true' : 'false';
    for (final f in files) {
      req.files.add(http.MultipartFile.fromBytes('files', f.bytes, filename: f.name));
    }
    final streamed = await req.send();
    return Job.fromJson(await _json(await http.Response.fromStream(streamed)));
  }

  Future<Job> job(String id) async {
    final r = await http.get(base.resolve('api/jobs/$id'), headers: _headers);
    return Job.fromJson(await _json(r));
  }

  Future<Uint8List> result(Job job) async {
    final r = await http.get(base.resolve(job.result!.replaceFirst('/', '')), headers: _headers);
    if (r.statusCode >= 400) throw ApiException('Download fehlgeschlagen (${r.statusCode})');
    return r.bodyBytes;
  }
}
