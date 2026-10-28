import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sheet2xml_app/main.dart';
import 'package:sheet2xml_app/settings.dart';

void main() {
  testWidgets('shows the upload screen and asks for a server', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final settings = Settings();
    await settings.load();
    await tester.pumpWidget(SheetApp(settings: settings));
    await tester.pump();
    expect(find.text('sheet2xml'), findsOneWidget);
    expect(find.text('Dateien wählen'), findsOneWidget);
    expect(find.text('Kein Server eingestellt'), findsOneWidget);
  });
}
