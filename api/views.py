from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ExtractedRecord, ExtractionJob
from .serializers import (
    ActionMessageSerializer,
    ErrorSerializer,
    ExtractedRecordSerializer,
    ExtractedResultSerializer,
    HealthCheckSerializer,
    JobListSerializer,
    JobStatisticsSerializer,
    JobStatusSerializer,
    JobSummarySerializer,
    ScanStartSerializer,
    UserSerializer,
)
from .services import (
    HubSpotAPIError,
    HubSpotDealsService,
    InvalidTokenError,
    RateLimitError,
    start_extraction_async,
    token_fingerprint,
)

JOB_ID_PARAMETER = openapi.Parameter(
    'job_id',
    openapi.IN_PATH,
    description='Unique extraction job identifier.',
    type=openapi.TYPE_STRING,
    format='uuid',
    required=True,
)
LIMIT_PARAMETER = openapi.Parameter(
    'limit',
    openapi.IN_QUERY,
    description='Maximum number of records to return (1-500).',
    type=openapi.TYPE_INTEGER,
    default=100,
)
OFFSET_PARAMETER = openapi.Parameter(
    'offset',
    openapi.IN_QUERY,
    description='Number of records to skip.',
    type=openapi.TYPE_INTEGER,
    default=0,
)
STATUS_FILTER_PARAMETER = openapi.Parameter(
    'status',
    openapi.IN_QUERY,
    description='Filter jobs by status.',
    type=openapi.TYPE_STRING,
    enum=[choice for choice, _ in ExtractionJob.Status.choices],
)


class HealthCheckViewSet(viewsets.ViewSet):
    serializer_class = HealthCheckSerializer

    @swagger_auto_schema(
        tags=['Health'],
        operation_summary='Service health check',
        operation_description='Returns the current status of the backend API.',
        responses={200: HealthCheckSerializer},
    )
    def list(self, request):
        return Response({
            'status': 'ok',
            'message': 'Backend API is running successfully'
        })

    @swagger_auto_schema(
        tags=['Health'],
        operation_summary='Ping the service',
        operation_description='Simple ping endpoint used to verify the service is reachable.',
        responses={200: openapi.Response(
            description='Pong response',
            examples={'application/json': {'message': 'pong'}},
        )},
    )
    @action(detail=False, methods=['get'])
    def ping(self, request):
        return Response({'message': 'pong'})


@swagger_auto_schema(tags=['Users'])
class UserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.order_by('id')
    serializer_class = UserSerializer


class V1HealthView(APIView):
    @swagger_auto_schema(
        tags=['Health'],
        operation_summary='Service health check (v1)',
        responses={200: HealthCheckSerializer},
    )
    def get(self, request):
        return Response({
            'status': 'ok',
            'message': 'Data extraction service is running successfully',
        })


class ScanStartView(APIView):
    @swagger_auto_schema(
        tags=['Extraction'],
        operation_summary='Start a new extraction job',
        operation_description=(
            'Creates an asynchronous HubSpot deals extraction job. '
            'Provide a HubSpot private app access token in the body or the '
            '`X-API-Token` header. The token is never stored; only a SHA-256 '
            'fingerprint is kept for duplicate detection.'
        ),
        request_body=ScanStartSerializer,
        responses={
            202: JobStatusSerializer,
            400: ErrorSerializer,
            401: ErrorSerializer,
            409: ErrorSerializer,
            502: ErrorSerializer,
        },
    )
    def post(self, request):
        if not hasattr(request.data, 'get'):
            return Response(
                {'error': 'Request body must be a JSON object.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = request.data.copy()
        header_token = request.headers.get('X-API-Token')
        if header_token and not payload.get('api_token'):
            payload['api_token'] = header_token

        serializer = ScanStartSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        fingerprint = token_fingerprint(data['api_token'])
        active_job = ExtractionJob.objects.filter(
            token_fingerprint=fingerprint,
            status__in=[ExtractionJob.Status.PENDING, ExtractionJob.Status.IN_PROGRESS],
        ).first()
        if active_job is not None:
            return Response(
                {
                    'error': 'An extraction job is already in progress for this token.',
                    'job_id': str(active_job.job_id),
                },
                status=status.HTTP_409_CONFLICT,
            )

        if getattr(settings, 'HUBSPOT_VALIDATE_TOKEN_ON_START', True):
            try:
                HubSpotDealsService(data['api_token']).validate_token()
            except InvalidTokenError as exc:
                return Response(
                    {'error': str(exc)},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            except (HubSpotAPIError, RateLimitError) as exc:
                return Response(
                    {'error': f'Unable to validate API token: {exc}'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        parameters = {
            'properties': data.get('properties'),
            'limit': data.get('limit'),
            'archived': data.get('archived', False),
        }
        job = ExtractionJob.objects.create(
            token_fingerprint=fingerprint,
            parameters=parameters,
        )
        start_extraction_async(
            job.job_id,
            data['api_token'],
            properties=parameters['properties'],
            page_size=parameters['limit'],
        )

        return Response(
            JobStatusSerializer(job).data,
            status=status.HTTP_202_ACCEPTED,
        )


class ScanStatusView(APIView):
    @swagger_auto_schema(
        tags=['Extraction'],
        operation_summary='Get extraction job status',
        manual_parameters=[JOB_ID_PARAMETER],
        responses={200: JobStatusSerializer, 404: ErrorSerializer},
    )
    def get(self, request, job_id):
        job = get_object_or_404(ExtractionJob, pk=job_id)
        return Response(JobStatusSerializer(job).data)


class ScanResultView(APIView):
    @swagger_auto_schema(
        tags=['Extraction'],
        operation_summary='Get extraction results',
        operation_description=(
            'Returns extracted records for a completed job. Results are paginated '
            'with `limit` and `offset`. Incomplete jobs return 409 Conflict.'
        ),
        manual_parameters=[JOB_ID_PARAMETER, LIMIT_PARAMETER, OFFSET_PARAMETER],
        responses={
            200: ExtractedResultSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
        },
    )
    def get(self, request, job_id):
        job = get_object_or_404(ExtractionJob, pk=job_id)

        if job.status != ExtractionJob.Status.COMPLETED:
            return Response(
                {
                    'error': 'Job is not completed yet.',
                    'status': job.status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        limit, offset = _pagination(request)
        records = job.records.all()
        count = records.count()
        page = records[offset:offset + limit]
        next_offset = offset + limit if offset + limit < count else None

        return Response({
            'job_id': str(job.job_id),
            'count': count,
            'limit': limit,
            'offset': offset,
            'next_offset': next_offset,
            'results': ExtractedRecordSerializer(page, many=True).data,
        })


class ScanCancelView(APIView):
    @swagger_auto_schema(
        tags=['Extraction'],
        operation_summary='Cancel an extraction job',
        operation_description=(
            'Cancels a pending or in-progress job. Completed, failed, or already '
            'cancelled jobs return 409 Conflict.'
        ),
        manual_parameters=[JOB_ID_PARAMETER],
        responses={
            200: ActionMessageSerializer,
            404: ErrorSerializer,
            409: ErrorSerializer,
        },
    )
    def post(self, request, job_id):
        job = get_object_or_404(ExtractionJob, pk=job_id)

        cancellable = [ExtractionJob.Status.PENDING, ExtractionJob.Status.IN_PROGRESS]
        updated = ExtractionJob.objects.filter(
            pk=job.job_id, status__in=cancellable
        ).update(status=ExtractionJob.Status.CANCELLED, cancelled_at=timezone.now())

        if not updated:
            job.refresh_from_db()
            return Response(
                {
                    'error': f'Job cannot be cancelled in its current state: {job.status}.',
                    'job_id': str(job.job_id),
                    'status': job.status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        job.refresh_from_db()
        return Response({
            'job_id': str(job.job_id),
            'status': job.status,
            'message': 'Extraction job cancelled successfully.',
        })


class ScanRemoveView(APIView):
    @swagger_auto_schema(
        tags=['Extraction'],
        operation_summary='Remove an extraction job and its data',
        manual_parameters=[JOB_ID_PARAMETER],
        responses={204: 'No Content', 404: ErrorSerializer},
    )
    def delete(self, request, job_id):
        job = get_object_or_404(ExtractionJob, pk=job_id)
        job.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class JobListView(APIView):
    @swagger_auto_schema(
        tags=['Jobs'],
        operation_summary='List extraction jobs',
        manual_parameters=[
            LIMIT_PARAMETER,
            OFFSET_PARAMETER,
            STATUS_FILTER_PARAMETER,
        ],
        responses={200: JobListSerializer, 400: ErrorSerializer},
    )
    def get(self, request):
        limit, offset = _pagination(request)
        jobs = ExtractionJob.objects.all()

        status_filter = request.query_params.get('status')
        if status_filter:
            valid_statuses = [choice for choice, _ in ExtractionJob.Status.choices]
            if status_filter not in valid_statuses:
                raise ValidationError({
                    'status': f'Invalid status. Choose from: {", ".join(valid_statuses)}.'
                })
            jobs = jobs.filter(status=status_filter)

        count = jobs.count()
        page = jobs[offset:offset + limit]
        next_offset = offset + limit if offset + limit < count else None

        return Response({
            'count': count,
            'limit': limit,
            'offset': offset,
            'next_offset': next_offset,
            'results': JobSummarySerializer(page, many=True).data,
        })


class JobStatisticsView(APIView):
    @swagger_auto_schema(
        tags=['Jobs'],
        operation_summary='Get extraction job statistics',
        responses={200: JobStatisticsSerializer},
    )
    def get(self, request):
        jobs = ExtractionJob.objects.all()
        counts = {
            row['status']: row['count']
            for row in jobs.values('status').annotate(count=Count('job_id'))
        }

        completed = jobs.filter(
            status=ExtractionJob.Status.COMPLETED,
            started_at__isnull=False,
            completed_at__isnull=False,
        )
        durations = [
            (job.completed_at - job.started_at).total_seconds()
            for job in completed
        ]

        return Response({
            'total_jobs': jobs.count(),
            'pending_jobs': counts.get(ExtractionJob.Status.PENDING, 0),
            'in_progress_jobs': counts.get(ExtractionJob.Status.IN_PROGRESS, 0),
            'completed_jobs': counts.get(ExtractionJob.Status.COMPLETED, 0),
            'failed_jobs': counts.get(ExtractionJob.Status.FAILED, 0),
            'cancelled_jobs': counts.get(ExtractionJob.Status.CANCELLED, 0),
            'total_records': ExtractedRecord.objects.count(),
            'average_extraction_time_seconds': (
                round(sum(durations) / len(durations), 2) if durations else None
            ),
        })


def _pagination(request, default_limit=100, max_limit=500):
    try:
        limit = int(request.query_params.get('limit', default_limit))
        offset = int(request.query_params.get('offset', 0))
    except (TypeError, ValueError):
        raise ValidationError({'pagination': 'limit and offset must be integers.'})

    if limit < 1 or limit > max_limit:
        raise ValidationError({'pagination': f'limit must be between 1 and {max_limit}.'})
    if offset < 0:
        raise ValidationError({'pagination': 'offset must be zero or greater.'})

    return limit, offset
