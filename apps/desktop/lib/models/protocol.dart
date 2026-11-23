class RynaStatus {
  final String version;
  final bool online;
  final String companionState;
  final bool emergencyStopped;
  final bool anyAiAvailable;
  final double ramTotalGb;
  final String recommendedTier;

  const RynaStatus({
    required this.version,
    required this.online,
    required this.companionState,
    required this.emergencyStopped,
    required this.anyAiAvailable,
    required this.ramTotalGb,
    required this.recommendedTier,
  });

  factory RynaStatus.fromJson(Map<String, dynamic> json) {
    final ai = (json['ai'] as Map<String, dynamic>?) ?? {};
    final hw = (json['hardware'] as Map<String, dynamic>?) ?? {};
    return RynaStatus(
      version: (json['version'] ?? '0.1.0').toString(),
      online: json['online'] == true,
      companionState: (json['companion_state'] ?? 'idle').toString(),
      emergencyStopped: json['emergency_stopped'] == true,
      anyAiAvailable: ai['any_provider_available'] == true,
      ramTotalGb: (hw['ram_total_gb'] as num?)?.toDouble() ?? 0.0,
      recommendedTier: (hw['recommended_tier'] ?? 'unknown').toString(),
    );
  }
}

class RynaMessage {
  final String id;
  final String role;
  final String content;
  final String? provider;
  final String? model;
  final String mode;

  const RynaMessage({
    required this.id,
    required this.role,
    required this.content,
    this.provider,
    this.model,
    required this.mode,
  });

  factory RynaMessage.fromJson(Map<String, dynamic> json) {
    return RynaMessage(
      id: (json['id'] ?? '').toString(),
      role: (json['role'] ?? 'assistant').toString(),
      content: (json['content'] ?? '').toString(),
      provider: json['provider']?.toString(),
      model: json['model']?.toString(),
      mode: (json['mode'] ?? 'offline').toString(),
    );
  }
}

class RynaTask {
  final String id;
  final String title;
  final String goal;
  final String status;
  final String outcomeStatus;
  final int permissionLevel;
  final String resultSummary;
  final String errorMessage;

  const RynaTask({
    required this.id,
    required this.title,
    required this.goal,
    required this.status,
    required this.outcomeStatus,
    required this.permissionLevel,
    required this.resultSummary,
    required this.errorMessage,
  });

  factory RynaTask.fromJson(Map<String, dynamic> json) {
    return RynaTask(
      id: (json['id'] ?? '').toString(),
      title: (json['title'] ?? '').toString(),
      goal: (json['goal'] ?? '').toString(),
      status: (json['status'] ?? 'pending').toString(),
      outcomeStatus: (json['outcome_status'] ?? 'pending').toString(),
      permissionLevel: (json['permission_level'] as num?)?.toInt() ?? 1,
      resultSummary: (json['result_summary'] ?? '').toString(),
      errorMessage: (json['error_message'] ?? '').toString(),
    );
  }
}

class RynaMemory {
  final String id;
  final String category;
  final String memoryType;
  final String content;
  final double confidence;

  const RynaMemory({
    required this.id,
    required this.category,
    required this.memoryType,
    required this.content,
    required this.confidence,
  });

  factory RynaMemory.fromJson(Map<String, dynamic> json) {
    return RynaMemory(
      id: (json['id'] ?? '').toString(),
      category: (json['category'] ?? 'personal').toString(),
      memoryType: (json['memory_type'] ?? 'confirmed_fact').toString(),
      content: (json['content'] ?? '').toString(),
      confidence: (json['confidence'] as num?)?.toDouble() ?? 0.8,
    );
  }
}
