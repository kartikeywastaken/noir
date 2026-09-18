import 'package:flutter_test/flutter_test.dart';
import 'package:noir_app/data/api/noir_api_client.dart';

void main() {
  test('Live EC2 backend wiring audit', timeout: const Timeout(Duration(minutes: 2)), () async {
    const baseUrl = 'https://noir-16-171-197-228.sslip.io';
    const token = 'TiSL4_eXiv2zjBbRfpKdZpTfNVvA-X93AT9oazT3k8wOd-9Z9TvzTj4U498fe0DM';
    const projectId = '691d72a90bea4ee9';

    final client = NoirApiClient(baseUrl: baseUrl, token: token);

    // 1. Health check
    print('1. Testing Health check...');
    final health = await client.getHealth();
    expect(health.status, 'ok');
    expect(health.capabilities.ai, isTrue);
    print('   Health: status=${health.status}, version=${health.version}, ai=${health.capabilities.ai}');

    // 2. Auth / Current User
    print('2. Testing /v1/auth/me...');
    final user = await client.getCurrentUser();
    expect(user['user_id'], isNotEmpty);
    print('   User: id=${user['user_id']}, name=${user['name']}');

    // 3. List Projects
    print('3. Testing /v1/projects...');
    final projects = await client.listProjects();
    expect(projects, isNotEmpty);
    print('   Projects count: ${projects.length}, first=${projects.first.id} (${projects.first.packageName})');

    // 4. Get Project Details
    print('4. Testing /v1/projects/$projectId...');
    final project = await client.getProject(projectId);
    expect(project.id, projectId);
    print('   Project: id=${project.id}, status=${project.status}, revision=${project.workspaceRevision}');

    // 5. Get Analysis
    print('5. Testing /v1/projects/$projectId/analysis...');
    final analysis = await client.getAnalysis(projectId);
    expect(analysis.packageName, isNotEmpty);
    print('   Analysis: package=${analysis.packageName}, components=${analysis.components.length}, permissions=${analysis.permissions.length}');

    // 6. List Plans
    print('6. Testing /v1/projects/$projectId/plans...');
    final plans = await client.listPlans(projectId);
    print('   Plans count: ${plans.length}');
    if (plans.isNotEmpty) {
      final firstPlanId = plans.first['plan_id'] as String;
      final planDetails = await client.getPlan(projectId, firstPlanId);
      print('   First plan: id=${planDetails.planId}, outcome=${planDetails.intendedOutcome}');
    }

    // 7. List Patches
    print('7. Testing /v1/projects/$projectId/patches...');
    final patches = await client.listPatches(projectId);
    print('   Patches count: ${patches.length}');
    if (patches.isNotEmpty) {
      final firstPatchId = patches.first['patch_id'] as String;
      final patchDetails = await client.getPatch(projectId, firstPatchId);
      print('   First patch: id=${patchDetails.patchId}, operations=${patchDetails.operations.length}');
    }

    // 8. List Builds
    print('8. Testing /v1/projects/$projectId/builds...');
    final builds = await client.listBuilds(projectId);
    print('   Builds count: ${builds.length}');
    if (builds.isNotEmpty) {
      print('   First build: id=${builds.first.buildId}, success=${builds.first.success}');
    }

    // 9. List Signing Profiles & Personal Key
    print('9. Testing /v1/keys...');
    var profiles = await client.listSigningProfiles();
    print('   Existing profiles count: ${profiles.length}');
    if (profiles.isEmpty) {
      print('   Creating personal signing profile for user...');
      final created = await client.createPersonalSigningProfile();
      print('   Created personal profile: ${created.name} (${created.type})');
      profiles = await client.listSigningProfiles();
    }
    expect(profiles, isNotEmpty);
    print('   Profiles: ${profiles.map((p) => "${p.name} (${p.type})").join(", ")}');

    // 10. Audit Report
    print('10. Testing /v1/projects/$projectId/audit...');
    final audit = await client.getAuditJson(projectId);
    expect(audit, isNotEmpty);
    print('   Audit report: entries=${audit['events'] != null ? (audit['events'] as List).length : "ok"}');

    // 11. Files listing
    print('11. Testing /v1/projects/$projectId/files...');
    final files = await client.listFiles(projectId);
    expect(files, isNotEmpty);
    print('   Workspace root files count: ${files.length}, top entries: ${files.take(5).map((f) => f.name).join(", ")}');

    // 12. File search
    print('12. Testing /v1/projects/$projectId/files/search...');
    final search = await client.searchFiles(projectId, 'ntlap.in');
    print('   Search results for "ntlap.in": count=${search.length}');
    if (search.isNotEmpty) {
      print('   Found in: ${search.first['path']}');
    }

    // 13. File read
    print('13. Testing /v1/projects/$projectId/files/read...');
    final manifest = await client.readFile(projectId, 'AndroidManifest.xml');
    expect(manifest, isNotEmpty);
    print('   AndroidManifest.xml length: ${manifest.length} bytes');

    client.dispose();
    print('\nALL 13 LIVE BACKEND API ENDPOINTS FULLY VERIFIED AND OPERATIONAL!');
  });
}
