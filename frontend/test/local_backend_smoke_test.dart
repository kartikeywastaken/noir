// Explicit opt-in integration test; never targets the user's normal backend.
import 'dart:io';
import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:noir_app/data/api/noir_api_client.dart';
import 'package:noir_app/data/models/models.dart';

void main() {
  final url = Platform.environment['NOIR_SMOKE_URL'];
  test(
    'real backend import → manual edit → validate → rebuild → download → audit',
    () async {
      final api = NoirApiClient(
        baseUrl: url,
        token: Platform.environment['NOIR_SMOKE_TOKEN'],
      );
      addTearDown(api.dispose);
      expect(await api.testAuth(), true);
      final input = Platform.environment['NOIR_SMOKE_APK']!;
      Future<JobInfo> finish(JobInfo job) async {
        final deadline = DateTime.now().add(const Duration(seconds: 90));
        while (!job.isTerminal && DateTime.now().isBefore(deadline)) {
          await Future<void>.delayed(const Duration(milliseconds: 200));
          job = await api.getJob(job.jobId);
        }
        expect(job.state, 'succeeded', reason: job.errorMessage);
        return job;
      }

      final imported = await finish(
        await api.importApk(input, idempotencyKey: 'owned-fixture-import'),
      );
      final id = imported.projectId;
      final project = await api.getProject(id);
      expect(project.packageName, 'com.noir.testfixture');
      expect(project.originalFilename, File(input).uri.pathSegments.last);
      expect((await api.getAnalysis(id)).packageName, 'com.noir.testfixture');
      expect(
        (await api.listFiles(id)).any((f) => f.name == 'AndroidManifest.xml'),
        true,
      );
      final before = await api.readFile(id, 'AndroidManifest.xml');
      final modified = before.replaceFirst(
        RegExp(r'android:label="[^"]*"'),
        'android:label="NOIR smoke"',
      );
      expect(modified, isNot(before));
      await api.beginManualSession(id);
      expect((await api.getManualSession(id)).active, true);
      await api.replaceFile(
        id,
        'AndroidManifest.xml',
        modified,
        project.workspaceRevision,
      );
      await api.recordManualChanges(
        id,
        'Owned fixture label changed by real Dart client',
      );
      expect(
        (await api.getProject(id)).workspaceRevision,
        project.workspaceRevision + 1,
      );
      expect((await api.getManualSession(id)).active, false);
      expect((await api.validate(id)).passed, true);
      await finish(
        await api.startBuild(id, idempotencyKey: 'owned-fixture-build'),
      );
      final build = (await api.listBuilds(id)).first;
      expect(build.success, true);
      final apk = await api.downloadArtifact(
        id,
        build.buildId,
        artifact: 'unsigned',
      );
      expect(sha256.convert(apk).toString(), build.unsignedApkHash);
      expect((await api.getAuditMarkdown(id)), contains('Owned fixture label'));
      expect((await api.listEvents(id)).isNotEmpty, true);
    },
    skip: url == null
        ? 'Run backend/scripts/flutter_smoke.py for isolated real-tool integration.'
        : false,
    timeout: const Timeout(Duration(minutes: 3)),
  );
}
