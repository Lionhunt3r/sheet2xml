import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:share_plus/share_plus.dart';

import 'api.dart';
import 'settings.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final settings = Settings();
  settings.load().then((_) => runApp(SheetApp(settings: settings)));
}

const _seed = Color(0xFF1F3A5F);

bool get _isMobile =>
    !kIsWeb && (defaultTargetPlatform == TargetPlatform.android || defaultTargetPlatform == TargetPlatform.iOS);

class SheetApp extends StatelessWidget {
  const SheetApp({super.key, required this.settings});
  final Settings settings;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'sheet2xml',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorSchemeSeed: _seed, useMaterial3: true),
      darkTheme: ThemeData(colorSchemeSeed: _seed, brightness: Brightness.dark, useMaterial3: true),
      home: HomePage(settings: settings),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key, required this.settings});
  final Settings settings;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final List<Upload> _files = [];
  Health? _health;
  String? _healthError;
  bool _review = false;

  Settings get settings => widget.settings;

  @override
  void initState() {
    super.initState();
    settings.addListener(_checkServer);
    _checkServer();
  }

  @override
  void dispose() {
    settings.removeListener(_checkServer);
    super.dispose();
  }

  Future<void> _checkServer() async {
    if (!settings.configured) {
      setState(() {
        _health = null;
        _healthError = 'Kein Server eingestellt';
      });
      return;
    }
    try {
      final h = await Api(settings.server, settings.token).health();
      if (!mounted) return;
      setState(() {
        _health = h;
        _healthError = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _health = null;
        _healthError = 'Server nicht erreichbar';
      });
    }
  }

  void _add(List<Upload> picked) {
    setState(() {
      for (final f in picked) {
        // one PDF on its own, or several photos (the pages of one piece)
        if (f.isPdf || _files.any((x) => x.isPdf)) _files.clear();
        _files.add(f);
      }
    });
  }

  Future<void> _pickFiles() async {
    final picked = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['pdf', 'png', 'jpg', 'jpeg', 'heic', 'heif', 'tif', 'tiff', 'webp'],
    );
    final uploads = <Upload>[];
    for (final f in picked) {
      uploads.add(Upload(f.name, await f.readAsBytes()));
    }
    _add(uploads);
  }

  Future<void> _takePhoto() async {
    final photo = await ImagePicker().pickImage(source: ImageSource.camera);
    if (photo == null) return;
    final n = _files.where((f) => !f.isPdf).length + 1;
    _add([Upload('seite-$n.jpg', await photo.readAsBytes())]);
  }

  Future<void> _openSettings() async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => SettingsPage(settings: settings)));
  }

  Future<void> _convert() async {
    final again = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => JobPage(api: Api(settings.server, settings.token), files: List.of(_files), review: _review),
      ),
    );
    if (again == true) setState(_files.clear);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final ready = _health != null && _files.isNotEmpty;
    return Scaffold(
      appBar: AppBar(
        title: const Text('sheet2xml'),
        actions: [
          IconButton(icon: const Icon(Icons.settings_outlined), tooltip: 'Einstellungen', onPressed: _openSettings),
        ],
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 640),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text(
                  'PDF oder Fotos von Noten hochladen und eine MusicXML-Datei für Sibelius, MuseScore, Dorico oder Finale bekommen.',
                  style: theme.textTheme.bodyLarge?.copyWith(color: theme.colorScheme.onSurfaceVariant),
                ),
                const SizedBox(height: 12),
                _ServerStatus(health: _health, error: _healthError, onSettings: _openSettings, onRetry: _checkServer),
                const SizedBox(height: 12),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: [
                            OutlinedButton.icon(
                              onPressed: _pickFiles,
                              icon: const Icon(Icons.upload_file),
                              label: const Text('Dateien wählen'),
                            ),
                            if (_isMobile)
                              OutlinedButton.icon(
                                onPressed: _takePhoto,
                                icon: const Icon(Icons.photo_camera_outlined),
                                label: const Text('Foto aufnehmen'),
                              ),
                          ],
                        ),
                        const SizedBox(height: 8),
                        if (_files.isEmpty)
                          Padding(
                            padding: const EdgeInsets.symmetric(vertical: 16),
                            child: Text(
                              'Ein PDF oder mehrere Fotos (Seiten in der richtigen Reihenfolge).',
                              style: TextStyle(color: theme.colorScheme.onSurfaceVariant),
                            ),
                          )
                        else
                          ReorderableListView(
                            shrinkWrap: true,
                            physics: const NeverScrollableScrollPhysics(),
                            buildDefaultDragHandles: _files.length > 1,
                            onReorderItem: (a, b) => setState(() {
                              _files.insert(b, _files.removeAt(a));
                            }),
                            children: [
                              for (var i = 0; i < _files.length; i++)
                                ListTile(
                                  key: ObjectKey(_files[i]),
                                  contentPadding: EdgeInsets.zero,
                                  leading: _files[i].isPdf
                                      ? const Icon(Icons.picture_as_pdf_outlined)
                                      : ClipRRect(
                                          borderRadius: BorderRadius.circular(4),
                                          child: Image.memory(
                                            _files[i].bytes,
                                            width: 40,
                                            height: 40,
                                            fit: BoxFit.cover,
                                            errorBuilder: (_, _, _) => const Icon(Icons.image_outlined),
                                          ),
                                        ),
                                  title: Text(
                                    _files[i].isPdf ? _files[i].name : 'Seite ${i + 1}: ${_files[i].name}',
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                  trailing: IconButton(
                                    icon: const Icon(Icons.close),
                                    tooltip: 'Entfernen',
                                    onPressed: () => setState(() => _files.removeAt(i)),
                                  ),
                                ),
                            ],
                          ),
                        if (_health?.review == true)
                          SwitchListTile(
                            contentPadding: EdgeInsets.zero,
                            value: _review,
                            onChanged: (v) => setState(() => _review = v),
                            title: const Text('Mit Claude prüfen'),
                            subtitle: const Text(
                              'Auffällige Takte werden zusätzlich von der KI kontrolliert (dauert länger).',
                            ),
                          ),
                        const SizedBox(height: 8),
                        FilledButton.icon(
                          onPressed: ready ? _convert : null,
                          icon: const Icon(Icons.music_note),
                          label: const Text('Umwandeln'),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _ServerStatus extends StatelessWidget {
  const _ServerStatus({required this.health, required this.error, required this.onSettings, required this.onRetry});
  final Health? health;
  final String? error;
  final VoidCallback onSettings;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    if (health == null && error == null) return const LinearProgressIndicator();
    if (health == null) {
      return Card(
        color: scheme.errorContainer,
        child: ListTile(
          leading: Icon(Icons.cloud_off, color: scheme.onErrorContainer),
          title: Text(error!, style: TextStyle(color: scheme.onErrorContainer)),
          subtitle: Text(
            'Adresse des sheet2xml-Servers in den Einstellungen eintragen.',
            style: TextStyle(color: scheme.onErrorContainer),
          ),
          trailing: Wrap(
            children: [
              IconButton(icon: const Icon(Icons.refresh), tooltip: 'Erneut versuchen', onPressed: onRetry),
              IconButton(icon: const Icon(Icons.settings_outlined), tooltip: 'Einstellungen', onPressed: onSettings),
            ],
          ),
        ),
      );
    }
    final h = health!;
    return Wrap(
      spacing: 8,
      runSpacing: 4,
      children: [
        Chip(avatar: const Icon(Icons.cloud_done_outlined, size: 18), label: Text('Server verbunden (v${h.version})')),
        if (!h.omr) const Chip(avatar: Icon(Icons.info_outline, size: 18), label: Text('Nur PDFs aus Notenprogrammen')),
      ],
    );
  }
}

class JobPage extends StatefulWidget {
  const JobPage({super.key, required this.api, required this.files, required this.review});
  final Api api;
  final List<Upload> files;
  final bool review;

  @override
  State<JobPage> createState() => _JobPageState();
}

class _JobPageState extends State<JobPage> {
  Job? _job;
  String? _error;
  Uint8List? _bytes;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _start();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _start() async {
    try {
      final job = await widget.api.createJob(widget.files, review: widget.review);
      setState(() => _job = job);
      _poll();
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  Future<void> _poll() async {
    try {
      final job = await widget.api.job(_job!.id);
      if (!mounted) return;
      if (job.status == 'done') {
        final bytes = await widget.api.result(job);
        if (!mounted) return;
        setState(() {
          _job = job;
          _bytes = bytes;
        });
      } else if (job.status == 'error') {
        setState(() {
          _job = job;
          _error = job.error;
        });
      } else {
        setState(() => _job = job);
        _timer = Timer(const Duration(milliseconds: 1500), _poll);
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    }
  }

  Future<void> _save() async {
    final uri = await FilePicker.saveFile(
      fileName: _job!.filename,
      bytes: _bytes!,
      mimeType: 'application/vnd.recordare.musicxml+xml',
      dialogTitle: 'MusicXML speichern',
    );
    if (uri != null && mounted && !kIsWeb) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Gespeichert')));
    }
  }

  Future<void> _share() async {
    final file = XFile.fromData(_bytes!, name: _job!.filename, mimeType: 'application/vnd.recordare.musicxml+xml');
    await SharePlus.instance.share(ShareParams(files: [file], fileNameOverrides: [_job!.filename]));
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final job = _job;
    final done = _bytes != null;
    return Scaffold(
      appBar: AppBar(
        title: Text(
          done
              ? 'Fertig'
              : _error != null
              ? 'Fehler'
              : 'Wird umgewandelt',
        ),
      ),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 640),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                if (!done && _error == null) const LinearProgressIndicator(),
                if (job == null && _error == null)
                  const Padding(padding: EdgeInsets.only(top: 16), child: Text('Wird hochgeladen …')),
                if (job != null)
                  for (final m in job.messages)
                    ListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.chevron_right),
                      title: Text(m),
                    ),
                if (_error != null)
                  Card(
                    color: theme.colorScheme.errorContainer,
                    child: ListTile(
                      leading: Icon(Icons.error_outline, color: theme.colorScheme.onErrorContainer),
                      title: Text(_error!, style: TextStyle(color: theme.colorScheme.onErrorContainer)),
                    ),
                  ),
                if (done) ...[
                  const SizedBox(height: 8),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(job!.title.isNotEmpty ? job.title : job.filename, style: theme.textTheme.titleLarge),
                          const SizedBox(height: 4),
                          Text(
                            [
                              if (job.parts.isNotEmpty) '${job.parts.length} Stimme(n): ${job.parts.join(', ')}',
                              if (job.measures > 0) '${job.measures} Takte',
                              job.engine == 'omr'
                                  ? 'Notenerkennung (Scan/Foto), bitte gegenlesen'
                                  : 'direkt aus dem PDF gelesen',
                            ].join(' · '),
                          ),
                          if (job.flaggedMeasures > 0 || job.warnings.isNotEmpty) ...[
                            const SizedBox(height: 8),
                            Text(
                              [
                                if (job.flaggedMeasures > 0)
                                  '${job.flaggedMeasures} Takt(e) mit unvollständigem Rhythmus, bitte prüfen.',
                                ...job.warnings,
                              ].join(' '),
                              style: TextStyle(color: theme.colorScheme.onSurfaceVariant),
                            ),
                          ],
                          const SizedBox(height: 16),
                          Wrap(
                            spacing: 8,
                            runSpacing: 8,
                            children: [
                              FilledButton.icon(
                                onPressed: _save,
                                icon: const Icon(Icons.download),
                                label: const Text('Speichern'),
                              ),
                              if (_isMobile)
                                OutlinedButton.icon(
                                  onPressed: _share,
                                  icon: const Icon(Icons.ios_share),
                                  label: const Text('Teilen / Öffnen in …'),
                                ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
                if (done || _error != null) ...[
                  const SizedBox(height: 16),
                  TextButton.icon(
                    onPressed: () => Navigator.of(context).pop(done),
                    icon: const Icon(Icons.add),
                    label: Text(done ? 'Neue Noten' : 'Zurück'),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key, required this.settings});
  final Settings settings;

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late final _server = TextEditingController(text: widget.settings.server);
  late final _token = TextEditingController(text: widget.settings.token);
  String? _status;
  bool _testing = false;

  Future<void> _test() async {
    setState(() {
      _testing = true;
      _status = null;
    });
    await widget.settings.save(_server.text, _token.text);
    try {
      final h = await Api(widget.settings.server, widget.settings.token).health();
      _status =
          'Verbunden, Version ${h.version}.'
          '${h.omr ? ' Scans und Fotos möglich.' : ' Nur PDFs aus Notenprogrammen.'}'
          '${h.review ? ' Claude-Prüfung verfügbar.' : ''}';
    } catch (e) {
      _status = 'Nicht erreichbar: $e';
    }
    if (mounted) setState(() => _testing = false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Einstellungen')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 640),
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                TextField(
                  controller: _server,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: 'Server-Adresse',
                    hintText: 'http://truenas.local:8000',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _token,
                  obscureText: true,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: 'Zugangsschlüssel (falls gesetzt)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                FilledButton(onPressed: _testing ? null : _test, child: const Text('Speichern und testen')),
                if (_status != null) Padding(padding: const EdgeInsets.only(top: 16), child: Text(_status!)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
